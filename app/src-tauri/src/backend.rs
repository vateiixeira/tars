// Conversa do processo principal com o backend, sem depender da janela principal (que pode estar
// escondida ou nem ter carregado): a bandeja e a regra de saída leem o status pelo mesmo
// WebSocket de eventos do front e fazem as poucas chamadas HTTP do menu.
use std::net::{SocketAddr, TcpStream};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::Deserialize;
use serde_json::Value;
use tungstenite::Message;

use crate::{ApiInfo, NOT_RUNNING};

const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
/// Sem nada do backend por este tempo: manda um ping; dois seguidos sem resposta, reconecta.
const READ_TIMEOUT: Duration = Duration::from_secs(15);
const RETRY_MIN: Duration = Duration::from_secs(1);
const RETRY_MAX: Duration = Duration::from_secs(10);
const HTTP_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum MeetingState {
    Idle,
    Recording,
    Processing,
}

/// O que a bandeja usa do `status` do backend.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BackendStatus {
    pub meeting: MeetingState,
    pub dictation_enabled: bool,
    /// `meeting.print_mode == "native"`: o print é tirado pelo próprio app (ausente vale false).
    pub print_native: bool,
}

#[derive(Deserialize)]
struct StatusPayload {
    dictation: DictationPart,
    meeting: MeetingPart,
}

#[derive(Deserialize)]
struct DictationPart {
    enabled: bool,
}

#[derive(Deserialize)]
struct MeetingPart {
    state: MeetingState,
    #[serde(default)]
    print_mode: Option<String>,
}

impl From<StatusPayload> for BackendStatus {
    fn from(p: StatusPayload) -> Self {
        BackendStatus {
            meeting: p.meeting.state,
            dictation_enabled: p.dictation.enabled,
            print_native: p.meeting.print_mode.as_deref() == Some("native"),
        }
    }
}

/// Mensagem do WebSocket: só o evento `status` interessa (níveis, alertas etc. são ignorados).
pub fn parse_event(text: &str) -> Option<BackendStatus> {
    let value: Value = serde_json::from_str(text).ok()?;
    if value.get("type")?.as_str()? != "status" {
        return None;
    }
    serde_json::from_value::<StatusPayload>(value).ok().map(Into::into)
}

pub fn events_url(info: &ApiInfo) -> String {
    // o token é `secrets.token_urlsafe`: não precisa de escape
    format!("ws://127.0.0.1:{}/api/events?token={}", info.port, info.token)
}

pub fn api_url(info: &ApiInfo, path: &str) -> String {
    format!("http://127.0.0.1:{}/api{path}", info.port)
}

/// Texto de erro de uma resposta HTTP sem sucesso: o `detail` do backend, senão o código.
fn error_detail(code: u16, body: Option<&Value>) -> String {
    match body.and_then(|b| b.get("detail")) {
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => format!("HTTP {code}"),
    }
}

pub enum Method {
    Get,
    Post,
    Put,
}

/// Chamada à API local; erro em texto pronto para mostrar ao usuário.
pub fn request(method: Method, path: &str, body: Option<Value>, timeout: Option<Duration>) -> Result<Value, String> {
    let info = crate::api_info()?;
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .timeout_global(Some(timeout.unwrap_or(HTTP_TIMEOUT)))
        .http_status_as_error(false)
        .build()
        .into();
    let url = api_url(&info, path);
    let auth = format!("Bearer {}", info.token);
    let body = body.unwrap_or(Value::Object(Default::default()));
    let result = match method {
        Method::Get => agent.get(&url).header("Authorization", &auth).call(),
        Method::Post => agent.post(&url).header("Authorization", &auth).send_json(&body),
        Method::Put => agent.put(&url).header("Authorization", &auth).send_json(&body),
    };
    let mut response = result.map_err(|_| NOT_RUNNING.to_string())?;
    let code = response.status();
    let json = response.body_mut().read_json::<Value>().ok();
    if code.is_success() {
        Ok(json.unwrap_or(Value::Null))
    } else {
        Err(error_detail(code.as_u16(), json.as_ref()))
    }
}

/// `GET /status` na hora (o "Sair" não confia só no último evento).
pub fn fetch_status() -> Option<BackendStatus> {
    let value = request(Method::Get, "/status", None, Some(Duration::from_secs(2))).ok()?;
    serde_json::from_value::<StatusPayload>(value).ok().map(Into::into)
}

/// Último status recebido pelo WebSocket (`None` = sem conexão com o backend).
#[derive(Default)]
pub struct Backend {
    status: Mutex<Option<BackendStatus>>,
}

impl Backend {
    pub fn current(&self) -> Option<BackendStatus> {
        *self.status.lock().unwrap()
    }

    fn set(&self, next: Option<BackendStatus>, on_change: &dyn Fn(Option<BackendStatus>)) {
        let changed = {
            let mut cur = self.status.lock().unwrap();
            let changed = *cur != next;
            *cur = next;
            changed
        };
        if changed {
            on_change(next);
        }
    }

    /// Thread que segue o WebSocket de eventos e reconecta (relendo o `api.json`, pois o token
    /// muda a cada início do backend). `on_change` roda a cada mudança de status.
    pub fn watch(self: Arc<Self>, on_change: impl Fn(Option<BackendStatus>) + Send + 'static) {
        std::thread::spawn(move || {
            let mut delay = RETRY_MIN;
            loop {
                let got_status = self.follow(&on_change);
                self.set(None, &on_change);
                delay = if got_status { RETRY_MIN } else { (delay * 2).min(RETRY_MAX) };
                std::thread::sleep(delay);
            }
        });
    }

    /// Uma conexão, até cair. Devolve se chegou a receber algum status.
    fn follow(&self, on_change: &dyn Fn(Option<BackendStatus>)) -> bool {
        let Ok(info) = crate::api_info() else { return false };
        let addr = SocketAddr::from(([127, 0, 0, 1], info.port));
        let Ok(stream) = TcpStream::connect_timeout(&addr, CONNECT_TIMEOUT) else { return false };
        if stream.set_read_timeout(Some(READ_TIMEOUT)).is_err() {
            return false;
        }
        let Ok((mut ws, _)) = tungstenite::client(events_url(&info), stream) else { return false };
        let mut got_status = false;
        let mut unanswered = 0;
        loop {
            match ws.read() {
                Ok(Message::Text(text)) => {
                    unanswered = 0;
                    if let Some(status) = parse_event(text.as_str()) {
                        got_status = true;
                        self.set(Some(status), on_change);
                    }
                }
                Ok(Message::Close(_)) => return got_status,
                Ok(_) => unanswered = 0, // pong, ping (respondido pela lib) ou binário
                Err(tungstenite::Error::Io(e))
                    if matches!(e.kind(), std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut) =>
                {
                    unanswered += 1;
                    if unanswered >= 2 || ws.send(Message::Ping(Default::default())).is_err() {
                        return got_status;
                    }
                }
                Err(_) => return got_status,
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_only_status_events() {
        let status = r#"{"type": "status", "dictation": {"enabled": true, "active": false, "key": "ctrl+super"},
            "meeting": {"state": "recording", "meeting_id": "m1", "started_at": "2026-10-20T10:00:00.123456",
            "followed_app": null, "alerts": ["mic_silent"]}}"#;
        assert_eq!(
            parse_event(status),
            Some(BackendStatus { meeting: MeetingState::Recording, dictation_enabled: true, print_native: false })
        );
        let processing = r#"{"type": "status", "dictation": {"enabled": false, "active": false, "key": "k"},
            "meeting": {"state": "processing", "meeting_id": null, "started_at": null, "followed_app": null, "alerts": []}}"#;
        assert_eq!(
            parse_event(processing),
            Some(BackendStatus { meeting: MeetingState::Processing, dictation_enabled: false, print_native: false })
        );
        assert_eq!(parse_event(r#"{"type": "levels", "mic_db": -20, "pc_db": -30}"#), None);
        assert_eq!(parse_event(r#"{"type": "dictation", "active": true}"#), None);
        // estado desconhecido ou lixo: ignora em vez de chutar
        assert_eq!(parse_event(r#"{"type": "status", "dictation": {"enabled": true}, "meeting": {"state": "x"}}"#), None);
        assert_eq!(parse_event("não é json"), None);
    }

    #[test]
    fn parses_print_mode_native_and_missing() {
        let with_mode = |mode: &str| {
            format!(
                r#"{{"type": "status", "dictation": {{"enabled": true}},
                "meeting": {{"state": "recording", "print_mode": {mode}}}}}"#
            )
        };
        assert!(parse_event(&with_mode(r#""native""#)).unwrap().print_native);
        assert!(!parse_event(&with_mode(r#""flameshot""#)).unwrap().print_native);
        assert!(!parse_event(&with_mode(r#""off""#)).unwrap().print_native);
        assert!(!parse_event(&with_mode("null")).unwrap().print_native);
        // backend antigo, sem o campo
        let old = r#"{"type": "status", "dictation": {"enabled": true}, "meeting": {"state": "recording"}}"#;
        assert!(!parse_event(old).unwrap().print_native);
    }

    #[test]
    fn builds_urls_like_the_front() {
        let info = ApiInfo { port: 47399, token: "abc-_1".into() };
        assert_eq!(events_url(&info), "ws://127.0.0.1:47399/api/events?token=abc-_1");
        assert_eq!(api_url(&info, "/meetings/start"), "http://127.0.0.1:47399/api/meetings/start");
    }

    #[test]
    fn error_detail_prefers_backend_message() {
        let body: Value = serde_json::json!({"detail": "Já existe uma reunião sendo gravada"});
        assert_eq!(error_detail(409, Some(&body)), "Já existe uma reunião sendo gravada");
        assert_eq!(error_detail(500, None), "HTTP 500");
    }
}

// Bandeja: ícone com o estado da reunião e o menu (reunião, print, abrir, ditado, sair). O estado vem
// do `Backend` (WebSocket do processo principal); as ações viram chamadas HTTP em thread própria
// para não travar o laço de eventos.
use std::sync::Arc;

use serde_json::json;
use tauri::image::Image;
use tauri::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tauri::tray::{TrayIcon, TrayIconBuilder};
use tauri::{AppHandle, Wry};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};

use crate::backend::{self, Backend, BackendStatus, MeetingState, Method};
use crate::NOT_RUNNING;

pub const APP_TITLE: &str = "TARS";
pub const STOP_BEFORE_EXIT: &str = "Pare a gravação antes de sair";

const ID_STATUS: &str = "status";
const ID_MEETING: &str = "meeting";
const ID_PRINT: &str = "print";
const ID_OPEN: &str = "open";
const ID_DICTATION: &str = "dictation";
const ID_QUIT: &str = "quit";

const ICON_IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const ICON_RECORDING: &[u8] = include_bytes!("../icons/tray-recording.png");
const ICON_PROCESSING: &[u8] = include_bytes!("../icons/tray-processing.png");

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TrayIconKind {
    Idle,
    Recording,
    Processing,
}

/// Tudo o que a bandeja mostra para um status (`None` = sem conexão com o backend).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrayView {
    pub icon: TrayIconKind,
    /// Primeira linha do menu, só informativa (no GNOME a bandeja não mostra dica de ferramenta).
    pub status_text: &'static str,
    pub meeting_text: &'static str,
    pub meeting_enabled: bool,
    /// "Tirar print": só gravando e com o print nativo ligado.
    pub print_enabled: bool,
    pub dictation_text: &'static str,
    pub dictation_enabled: bool,
}

pub fn tray_view(status: Option<BackendStatus>) -> TrayView {
    let Some(s) = status else {
        return TrayView {
            icon: TrayIconKind::Idle,
            status_text: NOT_RUNNING,
            meeting_text: "Iniciar reunião",
            meeting_enabled: false,
            print_enabled: false,
            dictation_text: "Pausar ditado",
            dictation_enabled: false,
        };
    };
    let (icon, status_text, meeting_text, meeting_enabled) = match s.meeting {
        MeetingState::Idle => (TrayIconKind::Idle, "Nenhuma reunião em andamento", "Iniciar reunião", true),
        MeetingState::Recording => (TrayIconKind::Recording, "Gravando reunião", "Parar reunião", true),
        // como o botão da janela principal: dá para iniciar a próxima enquanto a anterior processa
        // (reuniões em sequência; o backend põe o trabalho da GPU na fila)
        MeetingState::Processing => (TrayIconKind::Processing, "Processando reunião", "Iniciar reunião", true),
    };
    TrayView {
        icon,
        status_text,
        meeting_text,
        meeting_enabled,
        print_enabled: print_allowed(Some(s)),
        dictation_text: if s.dictation_enabled { "Pausar ditado" } else { "Retomar ditado" },
        dictation_enabled: true,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MeetingAction {
    Start,
    Stop,
}

/// O que o item de reunião faz agora (`None` = desabilitado).
pub fn meeting_action(status: Option<BackendStatus>) -> Option<MeetingAction> {
    match status?.meeting {
        MeetingState::Idle | MeetingState::Processing => Some(MeetingAction::Start),
        MeetingState::Recording => Some(MeetingAction::Stop),
    }
}

/// O item "Tirar print" só vale gravando com o print nativo (o backend recusa fora disso).
pub fn print_allowed(status: Option<BackendStatus>) -> bool {
    status.is_some_and(|s| s.meeting == MeetingState::Recording && s.print_native)
}

/// Novo valor de `dictation_enabled` ao clicar em pausar/retomar.
pub fn dictation_toggle(status: Option<BackendStatus>) -> Option<bool> {
    status.map(|s| !s.dictation_enabled)
}

/// "Sair" é bloqueado gravando: vale o status buscado na hora; sem resposta, o último recebido.
/// Sem nenhum dos dois o backend não está acessível e não há o que proteger daqui.
pub fn exit_allowed(fresh: Option<BackendStatus>, cached: Option<BackendStatus>) -> bool {
    fresh.or(cached).map_or(true, |s| s.meeting != MeetingState::Recording)
}

fn icon_bytes(kind: TrayIconKind) -> &'static [u8] {
    match kind {
        TrayIconKind::Idle => ICON_IDLE,
        TrayIconKind::Recording => ICON_RECORDING,
        TrayIconKind::Processing => ICON_PROCESSING,
    }
}

/// Ícone e itens que mudam com o status.
#[derive(Clone)]
pub struct Tray {
    icon: TrayIcon<Wry>,
    status: MenuItem<Wry>,
    meeting: MenuItem<Wry>,
    print: MenuItem<Wry>,
    dictation: MenuItem<Wry>,
}

impl Tray {
    pub fn build(app: &AppHandle, backend: Arc<Backend>) -> tauri::Result<Tray> {
        let view = tray_view(None);
        let status = MenuItem::with_id(app, ID_STATUS, view.status_text, false, None::<&str>)?;
        let meeting = MenuItem::with_id(app, ID_MEETING, view.meeting_text, view.meeting_enabled, None::<&str>)?;
        let print = MenuItem::with_id(app, ID_PRINT, "Tirar print", view.print_enabled, None::<&str>)?;
        let open = MenuItem::with_id(app, ID_OPEN, "Abrir", true, None::<&str>)?;
        let dictation =
            MenuItem::with_id(app, ID_DICTATION, view.dictation_text, view.dictation_enabled, None::<&str>)?;
        let quit = MenuItem::with_id(app, ID_QUIT, "Sair", true, None::<&str>)?;
        let menu = Menu::with_items(
            app,
            &[
                &status,
                &PredefinedMenuItem::separator(app)?,
                &meeting,
                &print,
                &open,
                &dictation,
                &PredefinedMenuItem::separator(app)?,
                &quit,
            ],
        )?;
        let icon = TrayIconBuilder::with_id("main")
            .icon(Image::from_bytes(icon_bytes(view.icon))?)
            .tooltip(APP_TITLE)
            .menu(&menu)
            .show_menu_on_left_click(true)
            .on_menu_event(move |app, event| on_menu(app, &backend, event))
            .build(app)?;
        Ok(Tray { icon, status, meeting, print, dictation })
    }

    pub fn apply(&self, view: &TrayView) {
        let results = [
            Image::from_bytes(icon_bytes(view.icon)).and_then(|i| self.icon.set_icon(Some(i))),
            self.status.set_text(view.status_text),
            self.meeting.set_text(view.meeting_text),
            self.meeting.set_enabled(view.meeting_enabled),
            self.print.set_enabled(view.print_enabled),
            self.dictation.set_text(view.dictation_text),
            self.dictation.set_enabled(view.dictation_enabled),
        ];
        for e in results.into_iter().filter_map(Result::err) {
            eprintln!("bandeja: falha ao atualizar: {e}");
        }
    }
}

fn on_menu(app: &AppHandle, backend: &Arc<Backend>, event: MenuEvent) {
    let id = event.id().as_ref();
    if id == ID_OPEN {
        crate::show_main(app);
        return;
    }
    let (app, backend, id) = (app.clone(), backend.clone(), id.to_string());
    std::thread::spawn(move || match id.as_str() {
        ID_MEETING => match meeting_action(backend.current()) {
            Some(MeetingAction::Start) => report(&app, "iniciar a reunião", backend::request(Method::Post, "/meetings/start", None, None)),
            Some(MeetingAction::Stop) => report(&app, "parar a reunião", backend::request(Method::Post, "/meetings/stop", None, None)),
            None => {}
        },
        ID_PRINT => {
            if print_allowed(backend.current()) {
                report(&app, "tirar o print", backend::request(Method::Post, "/meetings/print", None, None));
            }
        }
        ID_DICTATION => {
            if let Some(enabled) = dictation_toggle(backend.current()) {
                let body = json!({ "dictation_enabled": enabled });
                let what = if enabled { "retomar o ditado" } else { "pausar o ditado" };
                report(&app, what, backend::request(Method::Put, "/settings", Some(body), None));
            }
        }
        ID_QUIT => request_exit(&app, &backend),
        _ => {}
    });
}

fn report<T>(app: &AppHandle, what: &str, result: Result<T, String>) {
    if let Err(e) = result {
        warn(app, &format!("Não foi possível {what}: {e}"));
    }
}

fn warn(app: &AppHandle, message: &str) {
    app.dialog()
        .message(message)
        .title(APP_TITLE)
        .kind(MessageDialogKind::Warning)
        .buttons(MessageDialogButtons::Ok)
        .show(|_| {});
}

/// "Sair" (da bandeja ou ao fechar a janela sem bandeja): encerra o app e o overlay, não o
/// backend. Gravando, avisa e não sai. Bloqueia (HTTP): chamar fora da thread principal.
pub fn request_exit(app: &AppHandle, backend: &Backend) {
    if exit_allowed(backend::fetch_status(), backend.current()) {
        app.exit(0); // o `RunEvent::Exit` encerra o overlay
    } else {
        warn(app, STOP_BEFORE_EXIT);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn st(meeting: MeetingState, dictation_enabled: bool) -> Option<BackendStatus> {
        Some(BackendStatus { meeting, dictation_enabled, print_native: false })
    }

    fn st_print(meeting: MeetingState, print_native: bool) -> Option<BackendStatus> {
        Some(BackendStatus { meeting, dictation_enabled: true, print_native })
    }

    #[test]
    fn icon_and_meeting_item_follow_the_meeting_state() {
        let v = tray_view(st(MeetingState::Idle, true));
        assert_eq!((v.icon, v.meeting_text, v.meeting_enabled), (TrayIconKind::Idle, "Iniciar reunião", true));
        let v = tray_view(st(MeetingState::Recording, true));
        assert_eq!((v.icon, v.meeting_text, v.meeting_enabled), (TrayIconKind::Recording, "Parar reunião", true));
        let v = tray_view(st(MeetingState::Processing, true));
        // processando a anterior: já dá para iniciar a próxima (o trabalho da GPU vai para a fila)
        assert_eq!((v.icon, v.meeting_text, v.meeting_enabled), (TrayIconKind::Processing, "Iniciar reunião", true));
        assert_eq!(v.status_text, "Processando reunião");
    }

    #[test]
    fn dictation_item_toggles_label() {
        assert_eq!(tray_view(st(MeetingState::Idle, true)).dictation_text, "Pausar ditado");
        assert_eq!(tray_view(st(MeetingState::Idle, false)).dictation_text, "Retomar ditado");
        assert_eq!(dictation_toggle(st(MeetingState::Idle, true)), Some(false));
        assert_eq!(dictation_toggle(st(MeetingState::Recording, false)), Some(true));
        assert_eq!(dictation_toggle(None), None);
    }

    #[test]
    fn disconnected_disables_backend_actions() {
        let v = tray_view(None);
        assert_eq!(v.icon, TrayIconKind::Idle);
        assert_eq!(v.status_text, NOT_RUNNING);
        assert!(!v.meeting_enabled);
        assert!(!v.dictation_enabled);
        assert_eq!(meeting_action(None), None);
    }

    #[test]
    fn meeting_item_starts_or_stops() {
        assert_eq!(meeting_action(st(MeetingState::Idle, true)), Some(MeetingAction::Start));
        assert_eq!(meeting_action(st(MeetingState::Recording, true)), Some(MeetingAction::Stop));
        assert_eq!(meeting_action(st(MeetingState::Processing, true)), Some(MeetingAction::Start));
    }

    #[test]
    fn print_item_enabled_only_recording_native() {
        let enabled = |s| (tray_view(s).print_enabled, print_allowed(s));
        assert_eq!(enabled(st_print(MeetingState::Recording, true)), (true, true));
        assert_eq!(enabled(st_print(MeetingState::Idle, true)), (false, false));
        assert_eq!(enabled(st_print(MeetingState::Processing, true)), (false, false));
        assert_eq!(enabled(st_print(MeetingState::Recording, false)), (false, false));
        assert_eq!(enabled(None), (false, false));
    }

    #[test]
    fn exit_is_blocked_while_recording() {
        let rec = st(MeetingState::Recording, true);
        let idle = st(MeetingState::Idle, true);
        let processing = st(MeetingState::Processing, true);
        assert!(!exit_allowed(rec, idle));
        assert!(!exit_allowed(None, rec)); // sem resposta agora: vale o último status
        assert!(exit_allowed(idle, rec)); // a resposta na hora vence o evento velho
        assert!(exit_allowed(processing, None)); // processar continua no backend
        assert!(exit_allowed(None, None));
    }

    #[test]
    fn tray_icons_are_valid_pngs() {
        for kind in [TrayIconKind::Idle, TrayIconKind::Recording, TrayIconKind::Processing] {
            let img = Image::from_bytes(icon_bytes(kind)).unwrap();
            assert_eq!((img.width(), img.height()), (64, 64));
        }
    }
}

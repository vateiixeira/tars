// App desktop do whisper-ditado. A API local fica no backend Python; aqui só se lê o `api.json`
// que ele grava (porta e token).
//
// Dois modos no mesmo binário:
// - normal: janela principal + bandeja + plugins; mantém o overlay rodando como processo filho.
//   Fechar a janela só a esconde; o app sai pelo "Sair" da bandeja (bloqueado gravando).
//   `--minimized` (início no login) sobe sem mostrar a janela;
// - `--overlay`: só a pílula sempre por cima. Roda em processo próprio, com `GDK_BACKEND=x11`,
//   porque no Wayland do GNOME uma janela comum não consegue ficar por cima nem deixar o clique
//   atravessar; pelo XWayland, sim.
use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

mod backend;
mod tray;

use serde::{Deserialize, Serialize};
use tauri::utils::config::WindowConfig;
use tauri::{AppHandle, Emitter, Manager, PhysicalPosition, RunEvent, WebviewWindow, WebviewWindowBuilder, WindowEvent};

use backend::Backend;
use tray::Tray;

pub const NOT_RUNNING: &str = "Serviço do ditado não está rodando";

/// O que o front precisa de `api.json` (o arquivo também traz o `pid`, só usado aqui).
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ApiInfo {
    pub port: u16,
    pub token: String,
}

#[derive(Deserialize)]
struct ApiFile {
    port: u16,
    token: String,
    pid: Option<u32>,
}

/// `$XDG_DATA_HOME/whisper-ditado/api.json` (fallback `~/.local/share/...`), igual a `paths.py`.
fn api_info_path_from(xdg_data_home: Option<OsString>, home: Option<OsString>) -> Option<PathBuf> {
    let base = match xdg_data_home.filter(|v| !v.is_empty()) {
        Some(xdg) => PathBuf::from(xdg),
        None => PathBuf::from(home.filter(|v| !v.is_empty())?).join(".local").join("share"),
    };
    Some(base.join("whisper-ditado").join("api.json"))
}

fn api_info_path() -> Option<PathBuf> {
    api_info_path_from(std::env::var_os("XDG_DATA_HOME"), std::env::var_os("HOME"))
}

/// Lê `api.json`; arquivo ausente ou de um processo que já morreu (ex.: SIGKILL, sem limpar o
/// arquivo) vira `NOT_RUNNING`.
fn read_api_info(path: &Path, proc_root: &Path) -> Result<ApiInfo, String> {
    let raw = match std::fs::read_to_string(path) {
        Ok(raw) => raw,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Err(NOT_RUNNING.into()),
        Err(e) => return Err(format!("Não foi possível ler {}: {e}", path.display())),
    };
    let file: ApiFile = serde_json::from_str(&raw)
        .map_err(|e| format!("Arquivo {} inválido: {e}", path.display()))?;
    if let Some(pid) = file.pid {
        if proc_root.is_dir() && !proc_root.join(pid.to_string()).exists() {
            return Err(NOT_RUNNING.into());
        }
    }
    Ok(ApiInfo { port: file.port, token: file.token })
}

#[tauri::command]
fn api_info() -> Result<ApiInfo, String> {
    let path = api_info_path().ok_or_else(|| NOT_RUNNING.to_string())?;
    read_api_info(&path, Path::new("/proc"))
}

pub const OVERLAY_ARG: &str = "--overlay";
pub const MINIMIZED_ARG: &str = "--minimized";
const MAIN_LABEL: &str = "main";
const OVERLAY_LABEL: &str = "overlay";
/// Pid do app principal, passado ao overlay: ele vigia esse pid desde o primeiro instante.
const PARENT_PID_ENV: &str = "WHISPER_DITADO_APP_PID";
/// Distância (px lógicos) da pílula até a borda direita e até a barra superior.
const OVERLAY_MARGIN: f64 = 16.0;
const DMABUF_RENDERER_ENV: &str = "WEBKIT_DISABLE_DMABUF_RENDERER";

/// O caminho DMA-BUF do WebKitGTK pode deixar partes da janela pretas com NVIDIA.
/// Respeita um ajuste explícito do usuário e mantém o overlay transparente no caminho original.
/// https://tauri.app/es/develop/debug/linux-graphics/
fn needs_dmabuf_fallback(overlay: bool, nvidia: bool, configured: Option<&OsStr>) -> bool {
    !overlay && nvidia && configured.is_none()
}

const IM_MODULE_ENV: &str = "GTK_IM_MODULE";
/// Escolha explícita do método de entrada só para o TARS (por exemplo `ibus`, para quem usa um).
const IM_MODULE_CHOICE_ENV: &str = "TARS_GTK_IM_MODULE";
const SIMPLE_IM_MODULE: &str = "gtk-im-context-simple";

/// Com o IBus, o WebKitGTK abre uma janela de candidatos vazia (com setas) ao digitar acentos.
/// O método simples do GTK já trata teclas mortas e compose, então ele substitui o IBus, salvo
/// escolha em `TARS_GTK_IM_MODULE`. Devolve o valor a gravar em `GTK_IM_MODULE`, se houver.
fn im_module_override(inherited: Option<&OsStr>, chosen: Option<&OsStr>) -> Option<OsString> {
    if let Some(chosen) = chosen {
        return Some(chosen.to_owned());
    }
    (inherited == Some(OsStr::new("ibus"))).then(|| OsString::from(SIMPLE_IM_MODULE))
}

fn has_flag<S: AsRef<OsStr>>(args: impl IntoIterator<Item = S>, flag: &str) -> bool {
    args.into_iter().skip(1).any(|a| a.as_ref() == flag)
}

fn is_overlay_mode(args: impl IntoIterator<Item = OsString>) -> bool {
    has_flag(args, OVERLAY_ARG)
}

/// Início no login (o autostart registra o app com `--minimized`): sem janela principal.
fn is_minimized<S: AsRef<OsStr>>(args: impl IntoIterator<Item = S>) -> bool {
    has_flag(args, MINIMIZED_ARG)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let overlay = is_overlay_mode(std::env::args_os());
    let inherited_dmabuf = std::env::var_os(DMABUF_RENDERER_ENV);
    #[cfg(target_os = "linux")]
    if needs_dmabuf_fallback(overlay, Path::new("/sys/module/nvidia").exists(), inherited_dmabuf.as_deref()) {
        // SAFETY: ponto de entrada, antes de iniciar GTK, WebKit, plugins ou qualquer thread.
        unsafe { std::env::set_var(DMABUF_RENDERER_ENV, "1") };
        eprintln!("renderização: compatibilidade WebKitGTK/NVIDIA ativada na janela principal");
    }
    #[cfg(target_os = "linux")]
    if let Some(module) = im_module_override(
        std::env::var_os(IM_MODULE_ENV).as_deref(),
        std::env::var_os(IM_MODULE_CHOICE_ENV).as_deref(),
    ) {
        // SAFETY: idem; o overlay, processo filho, herda o mesmo valor.
        unsafe { std::env::set_var(IM_MODULE_ENV, module) };
    }
    if overlay {
        run_overlay();
    } else {
        run_main(inherited_dmabuf);
    }
}

/// Estado do processo principal acessível de janela, bandeja e eventos.
struct MainState {
    backend: Arc<Backend>,
    /// Bandeja criada: fechar a janela pode só escondê-la (senão não haveria como voltar).
    tray_ok: AtomicBool,
}

/// Avisa o front que a janela principal foi mostrada/escondida (o WebKitGTK nem sempre marca
/// `document.hidden` ao esconder; a seção Áudio desliga o monitor de níveis com isso).
const MAIN_VISIBILITY_EVENT: &str = "main-visibility";

fn notify_main_visibility(window: &WebviewWindow, visible: bool) {
    let _ = window.emit_to(MAIN_LABEL, MAIN_VISIBILITY_EVENT, visible);
}

/// Traz a janela principal à frente ("Abrir" da bandeja, segunda instância).
pub(crate) fn show_main(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN_LABEL) {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
        notify_main_visibility(&window, true);
    }
}

fn run_main(inherited_dmabuf: Option<OsString>) {
    let overlay = Arc::new(OverlayProcess { inherited_dmabuf, ..Default::default() });
    let supervisor = overlay.clone();
    let backend = Arc::new(Backend::default());
    let minimized = is_minimized(std::env::args_os());
    let app = tauri::Builder::default()
        // precisa ser o primeiro plugin: a segunda instância só traz a janela existente à frente.
        // Só no modo normal: o processo `--overlay` não é uma "segunda instância" do app.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| {
            // o autostart rodando com o app já aberto não deve abrir a janela
            if !is_minimized(&args) {
                show_main(app);
            }
        }))
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            Some(vec![MINIMIZED_ARG]),
        ))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_opener::init())
        .manage(MainState { backend: backend.clone(), tray_ok: AtomicBool::new(false) })
        .invoke_handler(tauri::generate_handler![api_info])
        .on_window_event(|window, event| {
            if window.label() != MAIN_LABEL {
                return;
            }
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let app = window.app_handle().clone();
                let state = app.state::<MainState>();
                if state.tray_ok.load(Ordering::SeqCst) {
                    let _ = window.hide();
                    if let Some(main) = app.get_webview_window(MAIN_LABEL) {
                        notify_main_visibility(&main, false);
                    }
                } else {
                    // sem bandeja, fechar é sair (com a mesma proteção do "Sair")
                    let backend = state.backend.clone();
                    std::thread::spawn(move || tray::request_exit(&app, &backend));
                }
            }
        })
        .setup(move |app| {
            supervisor.supervise();
            match Tray::build(app.handle(), backend.clone()) {
                Ok(tray) => {
                    app.state::<MainState>().tray_ok.store(true, Ordering::SeqCst);
                    tray.apply(&tray::tray_view(None));
                    backend.clone().watch(move |status| tray.apply(&tray::tray_view(status)));
                }
                Err(e) => eprintln!("bandeja: não foi possível criar o ícone: {e}"),
            }
            // a janela nasce escondida (`visible: false`); com `--minimized` fica assim
            if !minimized {
                show_main(app.handle());
            }
            configure_autostart(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("erro ao iniciar o app");
    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            overlay.stop();
        }
    });
}

/// Liga o início no login uma vez e, enquanto ligado, mantém o caminho do binário atualizado;
/// se o usuário o desligar (apagando o `.desktop`), respeita.
fn autostart_should_enable(marker_exists: bool, enabled: bool) -> bool {
    !marker_exists || enabled
}

/// Só no binário de release: o de debug (`target/debug`) não pode virar o app do login.
fn configure_autostart(app: &AppHandle) {
    if cfg!(debug_assertions) {
        return;
    }
    use tauri_plugin_autostart::ManagerExt;
    let Ok(dir) = app.path().app_config_dir() else { return };
    let marker = dir.join("autostart-configurado");
    let launcher = app.autolaunch();
    let enabled = launcher.is_enabled().unwrap_or(false);
    if !autostart_should_enable(marker.exists(), enabled) {
        return;
    }
    if let Err(e) = launcher.enable() {
        eprintln!("autostart: não foi possível ligar: {e}");
        return;
    }
    let _ = std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(&marker, b""));
}

/// Modo `--overlay`: cria só a janela `overlay` do `tauri.conf.json` (que lá tem `create: false`),
/// escondida. Quem decide mostrar/esconder é o `Overlay.vue` (`show_overlay` / `hide()`).
fn run_overlay() {
    // antes de tudo: se o app principal já morreu, nem chega a abrir a janela
    exit_with_parent();
    let mut context = tauri::generate_context!();
    // a janela principal (create: true) não pode nascer neste processo
    context.config_mut().app.windows.retain(|w| w.label == OVERLAY_LABEL);
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![api_info, show_overlay])
        .setup(|app| {
            let config = overlay_config(app.handle())
                .ok_or("janela do overlay ausente no tauri.conf.json")?;
            // perfil do WebKit separado do da janela principal (outro processo usando o mesmo)
            let data_dir = app.path().app_local_data_dir()?.join("overlay-webview");
            WebviewWindowBuilder::from_config(app.handle(), &config)?
                .data_directory(data_dir)
                .build()?;
            Ok(())
        })
        .run(context)
        .expect("erro ao iniciar o overlay");
}

fn overlay_config(app: &AppHandle) -> Option<WindowConfig> {
    app.config().app.windows.iter().find(|w| w.label == OVERLAY_LABEL).cloned()
}

/// Etapas de mostrar a pílula; separadas da janela real para testar a ordem e as falhas.
trait OverlayOps {
    fn show(&self) -> Result<(), String>;
    fn place(&self) -> Result<(), String>;
    fn on_top(&self) -> Result<(), String>;
    fn pass_clicks(&self) -> Result<(), String>;
    fn hide(&self) -> Result<(), String>;
}

/// Se posicionar falhar, a pílula fica onde o mutter pôs, mas ainda por cima e sem capturar
/// clique. Sem o recorte de entrada ela não pode ficar visível: esconde. Qualquer falha volta
/// como erro para o front tentar de novo.
fn show_overlay_steps(w: &impl OverlayOps) -> Result<(), String> {
    w.show()?; // não mapeou: nada visível (e o recorte dá panic sem janela mapeada)
    let mut errors = Vec::new();
    for step in [w.place(), w.on_top()] {
        if let Err(e) = step {
            errors.push(e);
        }
    }
    if let Err(e) = w.pass_clicks() {
        errors.push(e);
        if let Err(e) = w.hide() {
            errors.push(e);
        }
    }
    if errors.is_empty() { Ok(()) } else { Err(errors.join("; ")) }
}

struct RealOverlay<'a> {
    window: &'a WebviewWindow,
    width: f64,
}

impl OverlayOps for RealOverlay<'_> {
    fn show(&self) -> Result<(), String> {
        self.window.show().map_err(|e| format!("mostrar: {e}"))
    }
    fn place(&self) -> Result<(), String> {
        place_overlay(self.window, self.width).map_err(|e| format!("posicionar: {e}"))
    }
    fn on_top(&self) -> Result<(), String> {
        self.window.set_always_on_top(true).map_err(|e| format!("sempre por cima: {e}"))
    }
    fn pass_clicks(&self) -> Result<(), String> {
        self.window.set_ignore_cursor_events(true).map_err(|e| format!("clique atravessando: {e}"))
    }
    fn hide(&self) -> Result<(), String> {
        self.window.hide().map_err(|e| format!("esconder: {e}"))
    }
}

/// Mostra a pílula e reaplica tudo a cada vez: ao ser mapeada de novo, a janela perde a posição
/// (o mutter recentraliza) e o "sempre por cima"; o recorte de entrada (clique atravessa) só
/// pode ser aplicado com a janela mapeada.
#[tauri::command]
fn show_overlay(window: WebviewWindow) -> Result<(), String> {
    let width = overlay_config(window.app_handle()).map_or(360.0, |c| c.width);
    show_overlay_steps(&RealOverlay { window: &window, width }).inspect_err(|e| {
        eprintln!("overlay: falha ao mostrar: {e}");
    })
}

/// Canto superior direito da área útil do monitor (fora da barra superior do GNOME).
fn overlay_position(
    area: PhysicalPosition<i32>,
    area_width: u32,
    window_width: u32,
    scale: f64,
) -> PhysicalPosition<i32> {
    let margin = (OVERLAY_MARGIN * scale).round() as i32;
    PhysicalPosition::new(area.x + area_width as i32 - window_width as i32 - margin, area.y + margin)
}

fn place_overlay(window: &WebviewWindow, logical_width: f64) -> tauri::Result<()> {
    // no XWayland nem sempre há monitor "primário" definido: cai para o atual/primeiro
    let monitor = match window.primary_monitor()? {
        Some(m) => Some(m),
        None => match window.current_monitor()? {
            Some(m) => Some(m),
            None => window.available_monitors()?.into_iter().next(),
        },
    };
    let Some(monitor) = monitor else { return Ok(()) };
    let area = monitor.work_area();
    let scale = monitor.scale_factor();
    // a largura vem da config: antes de mapear, o GTK ainda informa tamanho 0
    let width = (logical_width * scale).round() as u32;
    window.set_position(overlay_position(area.position, area.size.width, width, scale))
}

/// Pid a vigiar: o que o app principal passou; rodado à mão (sem a variável), o pai do início.
fn expected_parent(env: Option<OsString>, initial_ppid: u32) -> u32 {
    env.and_then(|v| v.to_str()?.parse().ok()).unwrap_or(initial_ppid)
}

fn parent_alive(expected: u32, current_ppid: u32) -> bool {
    expected == current_ppid
}

/// O overlay não sobrevive ao app principal: se ele morrer sem passar pelo `RunEvent::Exit`
/// (SIGKILL, logout), o processo filho é adotado por outro e o `ppid` muda. Confere já na
/// largada (o pai pode ter morrido antes de o overlay chegar aqui) e depois a cada 1 s.
fn exit_with_parent() {
    #[cfg(unix)]
    {
        use std::os::unix::process::parent_id;
        let expected = expected_parent(std::env::var_os(PARENT_PID_ENV), parent_id());
        if !parent_alive(expected, parent_id()) {
            std::process::exit(0);
        }
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_secs(1));
            if !parent_alive(expected, parent_id()) {
                // processo sem estado: não há o que salvar
                std::process::exit(0);
            }
        });
    }
}

const OVERLAY_POLL: Duration = Duration::from_millis(500);
const OVERLAY_RESTART_MIN: Duration = Duration::from_secs(1);
const OVERLAY_RESTART_MAX: Duration = Duration::from_secs(30);
/// Rodou pelo menos isto antes de morrer: não é um laço de falhas, volta ao atraso mínimo.
const OVERLAY_STABLE: Duration = Duration::from_secs(30);

/// Processo filho `--overlay`: iniciado com o app, reiniciado se morrer, encerrado ao sair.
#[derive(Default)]
pub struct OverlayProcess {
    child: Mutex<Option<Child>>,
    stopping: AtomicBool,
    /// Valor anterior ao fallback da janela principal: não impõe DMA-BUF desligado ao overlay.
    inherited_dmabuf: Option<OsString>,
}

impl OverlayProcess {
    fn command(&self, executable: &Path) -> Command {
        let mut command = Command::new(executable);
        command.arg(OVERLAY_ARG)
            .env("GDK_BACKEND", "x11")
            .env(PARENT_PID_ENV, std::process::id().to_string());
        match &self.inherited_dmabuf {
            Some(value) => { command.env(DMABUF_RENDERER_ENV, value); }
            None => { command.env_remove(DMABUF_RENDERER_ENV); }
        }
        command
    }

    fn spawn(&self) -> std::io::Result<Child> {
        self.command(&std::env::current_exe()?).spawn()
    }

    /// Thread que mantém o overlay vivo. Morte rápida seguida dobra o atraso até 30 s.
    pub fn supervise(self: Arc<Self>) {
        std::thread::spawn(move || {
            let mut delay = OVERLAY_RESTART_MIN;
            loop {
                let started = Instant::now();
                {
                    let mut child = self.child.lock().unwrap();
                    if self.stopping.load(Ordering::SeqCst) {
                        return;
                    }
                    match self.spawn() {
                        Ok(c) => *child = Some(c),
                        Err(e) => eprintln!("overlay: não foi possível iniciar: {e}"),
                    }
                }
                // espera o filho morrer sem segurar o lock (o `stop` precisa dele)
                loop {
                    std::thread::sleep(OVERLAY_POLL);
                    let mut child = self.child.lock().unwrap();
                    if self.stopping.load(Ordering::SeqCst) {
                        return;
                    }
                    let Some(c) = child.as_mut() else { break };
                    match c.try_wait() {
                        Ok(None) => {}
                        Ok(Some(st)) => {
                            eprintln!("overlay: processo saiu ({st}); reiniciando");
                            *child = None;
                            break;
                        }
                        Err(e) => {
                            eprintln!("overlay: falha ao acompanhar o processo: {e}");
                            *child = None;
                            break;
                        }
                    }
                }
                delay = if started.elapsed() >= OVERLAY_STABLE {
                    OVERLAY_RESTART_MIN
                } else {
                    (delay * 2).min(OVERLAY_RESTART_MAX)
                };
                let until = Instant::now() + delay;
                while Instant::now() < until {
                    if self.stopping.load(Ordering::SeqCst) {
                        return;
                    }
                    std::thread::sleep(OVERLAY_POLL);
                }
            }
        });
    }

    /// Encerra o overlay e impede novos reinícios.
    pub fn stop(&self) {
        let mut child = self.child.lock().unwrap();
        self.stopping.store(true, Ordering::SeqCst);
        if let Some(mut c) = child.take() {
            let _ = c.kill();
            let _ = c.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dmabuf_fallback_is_limited_to_nvidia_main_without_an_explicit_setting() {
        assert!(needs_dmabuf_fallback(false, true, None));
        assert!(!needs_dmabuf_fallback(true, true, None));
        assert!(!needs_dmabuf_fallback(false, false, None));
        assert!(!needs_dmabuf_fallback(false, true, Some(OsStr::new("0"))));
        assert!(!needs_dmabuf_fallback(false, true, Some(OsStr::new("1"))));
    }

    #[test]
    fn ibus_is_replaced_by_the_simple_gtk_input_unless_tars_chooses_otherwise() {
        let simple = Some(OsString::from(SIMPLE_IM_MODULE));
        assert_eq!(im_module_override(Some(OsStr::new("ibus")), None), simple);
        assert_eq!(im_module_override(None, None), None);
        assert_eq!(im_module_override(Some(OsStr::new("fcitx")), None), None);
        assert_eq!(
            im_module_override(Some(OsStr::new("ibus")), Some(OsStr::new("ibus"))),
            Some(OsString::from("ibus"))
        );
        assert_eq!(
            im_module_override(None, Some(OsStr::new("xim"))),
            Some(OsString::from("xim"))
        );
    }

    #[test]
    fn overlay_does_not_inherit_the_main_windows_automatic_rendering_fallback() {
        for original in [None, Some(OsString::from("0")), Some(OsString::from("1"))] {
            let overlay = OverlayProcess { inherited_dmabuf: original.clone(), ..Default::default() };
            let command = overlay.command(Path::new("/app"));
            let renderer = command.get_envs().find(|(key, _)| *key == OsStr::new(DMABUF_RENDERER_ENV));
            assert_eq!(renderer, Some((OsStr::new(DMABUF_RENDERER_ENV), original.as_deref())));
            assert!(command.get_envs().any(|(key, value)| key == OsStr::new("GDK_BACKEND") && value == Some(OsStr::new("x11"))));
        }
    }

    #[test]
    fn path_prefers_xdg_data_home_and_falls_back_to_home() {
        assert_eq!(
            api_info_path_from(Some("/x".into()), Some("/home/u".into())),
            Some(PathBuf::from("/x/whisper-ditado/api.json"))
        );
        assert_eq!(
            api_info_path_from(Some("".into()), Some("/home/u".into())),
            Some(PathBuf::from("/home/u/.local/share/whisper-ditado/api.json"))
        );
        assert_eq!(api_info_path_from(None, None), None);
    }

    #[test]
    fn reads_port_and_token_and_reports_missing_service() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("api.json");
        let procs = dir.path().join("proc");
        assert_eq!(read_api_info(&path, &procs), Err(NOT_RUNNING.to_string()));

        std::fs::create_dir_all(procs.join("42")).unwrap();
        std::fs::write(&path, r#"{"port": 47399, "token": "abc", "pid": 42}"#).unwrap();
        assert_eq!(
            read_api_info(&path, &procs),
            Ok(ApiInfo { port: 47399, token: "abc".into() })
        );

        // pid de um processo que não existe mais: arquivo velho
        std::fs::write(&path, r#"{"port": 47399, "token": "abc", "pid": 43}"#).unwrap();
        assert_eq!(read_api_info(&path, &procs), Err(NOT_RUNNING.to_string()));
    }

    #[test]
    fn overlay_mode_only_with_flag() {
        let args = |v: &[&str]| v.iter().map(OsString::from).collect::<Vec<_>>();
        assert!(is_overlay_mode(args(&["app", "--overlay"])));
        assert!(!is_overlay_mode(args(&["app"])));
        assert!(!is_overlay_mode(args(&["app", "--minimized"])));
        // o próprio executável chamado "--overlay" não conta
        assert!(!is_overlay_mode(args(&["--overlay"])));
    }

    #[test]
    fn minimized_only_with_flag() {
        assert!(is_minimized(["app", "--minimized"]));
        assert!(!is_minimized(["app"]));
        assert!(!is_minimized(["--minimized"]));
    }

    #[test]
    fn overlay_watches_the_pid_given_by_the_parent() {
        // o pid vem do pai: se ele já morreu antes de o vigia começar, o ppid atual (adotado)
        // não bate e o overlay sai na hora
        assert_eq!(expected_parent(Some("4242".into()), 1), 4242);
        assert!(!parent_alive(4242, 1));
        assert!(parent_alive(4242, 4242));
        // rodado à mão (sem a variável) ou valor estragado: o ppid do início
        assert_eq!(expected_parent(None, 77), 77);
        assert_eq!(expected_parent(Some("x".into()), 77), 77);
    }

    #[test]
    fn autostart_is_set_once_and_kept_fresh_while_enabled() {
        assert!(autostart_should_enable(false, false)); // primeira vez
        assert!(autostart_should_enable(true, true)); // reescreve (caminho do binário atual)
        assert!(!autostart_should_enable(true, false)); // o usuário desligou: respeita
    }

    /// Janela falsa: registra as chamadas e falha nas etapas pedidas.
    #[derive(Default)]
    struct FakeOverlay {
        calls: std::cell::RefCell<Vec<&'static str>>,
        fail: Vec<&'static str>,
    }

    impl FakeOverlay {
        fn step(&self, name: &'static str) -> Result<(), String> {
            self.calls.borrow_mut().push(name);
            if self.fail.contains(&name) { Err(format!("{name} falhou")) } else { Ok(()) }
        }
    }

    impl OverlayOps for FakeOverlay {
        fn show(&self) -> Result<(), String> { self.step("show") }
        fn place(&self) -> Result<(), String> { self.step("place") }
        fn on_top(&self) -> Result<(), String> { self.step("on_top") }
        fn pass_clicks(&self) -> Result<(), String> { self.step("pass_clicks") }
        fn hide(&self) -> Result<(), String> { self.step("hide") }
    }

    #[test]
    fn overlay_keeps_on_top_and_click_through_even_if_placing_fails() {
        let w = FakeOverlay { fail: vec!["place"], ..Default::default() };
        assert!(show_overlay_steps(&w).is_err()); // erro volta para o front tentar de novo
        assert_eq!(*w.calls.borrow(), ["show", "place", "on_top", "pass_clicks"]);

        let ok = FakeOverlay::default();
        assert!(show_overlay_steps(&ok).is_ok());
        assert_eq!(*ok.calls.borrow(), ["show", "place", "on_top", "pass_clicks"]);
    }

    #[test]
    fn overlay_never_stays_visible_capturing_clicks() {
        let w = FakeOverlay { fail: vec!["pass_clicks"], ..Default::default() };
        assert!(show_overlay_steps(&w).is_err());
        assert_eq!(w.calls.borrow().last(), Some(&"hide"));

        // não mapeou: nada mais (o recorte de entrada dá panic sem janela mapeada)
        let w = FakeOverlay { fail: vec!["show"], ..Default::default() };
        assert!(show_overlay_steps(&w).is_err());
        assert_eq!(*w.calls.borrow(), ["show"]);
    }

    #[test]
    fn overlay_goes_to_top_right_of_work_area() {
        // 1920 de largura, barra superior de 32 px, janela de 360
        let p = overlay_position(PhysicalPosition::new(0, 32), 1920, 360, 1.0);
        assert_eq!(p, PhysicalPosition::new(1920 - 360 - 16, 32 + 16));
        // segundo monitor à direita, escala 2
        let p = overlay_position(PhysicalPosition::new(1920, 64), 3840, 720, 2.0);
        assert_eq!(p, PhysicalPosition::new(1920 + 3840 - 720 - 32, 64 + 32));
    }
}

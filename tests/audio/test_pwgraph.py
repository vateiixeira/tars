import copy
import json
from pathlib import Path

from whisper_ditado.audio import pwgraph
from whisper_ditado.audio.pwgraph import CallApp, Device, Target

FIXTURES = Path(__file__).parent.parent / "fixtures"
ANALOG = "alsa_output.pci-0000_09_00.4.analog-stereo"
HDMI = "alsa_output.pci-0000_07_00.1.hdmi-stereo"
MIC = "alsa_input.pci-0000_09_00.4.analog-stereo"
FIREFOX_PID = 4242


def load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


def stream_node(node_id: int, media_class: str, app: str, pid: int | None, name: str) -> dict:
    props = {
        "node.name": name,
        "media.class": media_class,
        "application.name": app,
        "object.id": node_id,
    }
    if pid is not None:
        props["application.process.id"] = pid
    return {"id": node_id, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def link(link_id: int, out_node: int, in_node: int) -> dict:
    return {
        "id": link_id,
        "type": "PipeWire:Interface:Link",
        "info": {"output-node-id": out_node, "input-node-id": in_node},
    }


def test_devices_lists_sources_and_sinks_with_defaults():
    graph = pwgraph.parse(load("pwdump_idle.json"))
    devs = pwgraph.devices(graph)
    assert {d.name for d in devs.sinks} == {ANALOG, HDMI, "obs_mudo"}
    assert MIC in {d.name for d in devs.sources}
    assert all(isinstance(d, Device) and d.description for d in devs.sinks)
    assert devs.default_sink == ANALOG
    assert devs.default_source == MIC
    # fontes de vídeo/MIDI não são microfones
    assert not any(d.name.startswith("v4l2") for d in devs.sources)


def test_devices_excludes_our_own_nodes():
    dump = load("pwdump_idle.json")
    dump.append(stream_node(300, "Audio/Sink", "x", None, "whisper_ditado_mix"))
    dump.append(stream_node(301, "Audio/Source", "x", None, "whisper_ditado_mic"))
    devs = pwgraph.devices(pwgraph.parse(dump))
    names = {d.name for d in devs.sinks + devs.sources}
    assert not any(n.startswith("whisper_ditado") for n in names)


def test_parse_reads_nodes_links_and_defaults():
    graph = pwgraph.parse(load("pwdump_call_firefox.json"))
    assert (62, 120) in graph.links and (121, 61) in graph.links
    node = graph.nodes[120]
    assert (node.name, node.media_class) == ("Firefox", "Stream/Input/Audio")
    assert (node.app_name, node.app_pid, node.app_binary) == ("Firefox", FIREFOX_PID, "firefox")
    assert graph.default_sink == ANALOG
    assert graph.default_source == MIC


def test_call_app_detected_by_mic_capture():
    graph = pwgraph.parse(load("pwdump_call_firefox.json"))
    assert pwgraph.call_apps(graph, own_pids=set()) == [
        CallApp("Firefox", FIREFOX_PID, {ANALOG})
    ]


def test_browser_playing_without_mic_is_not_a_call_app():
    dump = [o for o in load("pwdump_call_firefox.json") if o["id"] not in (120, 130)]
    graph = pwgraph.parse(dump)
    assert 121 in graph.nodes
    assert pwgraph.call_apps(graph, own_pids=set()) == []


def test_capture_not_linked_to_a_source_is_not_a_call_app():
    dump = [o for o in load("pwdump_call_firefox.json") if o["id"] != 130]
    assert pwgraph.call_apps(pwgraph.parse(dump), own_pids=set()) == []


def test_own_pw_record_is_ignored():
    dump = load("pwdump_idle.json")
    dump += [stream_node(200, "Stream/Input/Audio", "pw-record", 9999, "pw-record"), link(201, 62, 200)]
    graph = pwgraph.parse(dump)
    assert pwgraph.call_apps(graph, own_pids=set())[0].pid == 9999
    assert pwgraph.call_apps(graph, own_pids={9999}) == []


def test_node_from_whisper_ditado_app_is_ignored():
    dump = load("pwdump_idle.json")
    dump += [stream_node(200, "Stream/Input/Audio", "whisper-ditado", 9999, "x"), link(201, 62, 200)]
    assert pwgraph.call_apps(pwgraph.parse(dump), own_pids=set()) == []


def test_dictation_recorder_is_not_a_call_app():
    # `pw-record` do ditado (processo filho, outro pid) com as propriedades de `capture.build_command`
    dump = load("pwdump_call_firefox.json")
    dump += [
        stream_node(200, "Stream/Input/Audio", "whisper-ditado", 9999, "whisper_ditado_dictation"),
        link(201, 62, 200),
    ]
    apps = pwgraph.call_apps(pwgraph.parse(dump), own_pids=set())
    assert apps == [CallApp("Firefox", FIREFOX_PID, {ANALOG})]


def test_most_recent_capture_first_and_app_name_fallback():
    dump = load("pwdump_call_firefox.json")
    # segundo app sem pid: saída associada pelo app_name
    dump += [
        stream_node(150, "Stream/Input/Audio", "Zoom", None, "zoom-in"),
        stream_node(151, "Stream/Output/Audio", "Zoom", None, "zoom-out"),
        link(152, 62, 150),
        link(153, 151, 66),
    ]
    apps = pwgraph.call_apps(pwgraph.parse(dump), own_pids=set())
    assert [a.app_name for a in apps] == ["Zoom", "Firefox"]
    assert apps[0] == CallApp("Zoom", None, {HDMI})


def test_pick_target_follows_call_app_sink():
    dump = copy.deepcopy(load("pwdump_call_firefox.json"))
    for o in dump:
        if o["id"] == 131:
            o["info"]["input-node-id"] = 66  # Firefox toca no HDMI
    graph = pwgraph.parse(dump)
    apps = pwgraph.call_apps(graph, own_pids=set())
    assert pwgraph.pick_pc_target(graph, apps, fixed_sink=None, follow=True) == Target(
        HDMI, "Firefox", False
    )


def test_pick_target_fixed_sink_reports_mismatch():
    graph = pwgraph.parse(load("pwdump_call_firefox.json"))
    apps = pwgraph.call_apps(graph, own_pids=set())
    assert pwgraph.pick_pc_target(graph, apps, fixed_sink=HDMI, follow=True) == Target(
        HDMI, None, True
    )
    assert pwgraph.pick_pc_target(graph, apps, fixed_sink=ANALOG, follow=True).mismatch is False
    assert pwgraph.pick_pc_target(graph, [], fixed_sink=HDMI, follow=True).mismatch is False


def test_fixed_sink_is_not_a_mismatch_while_call_app_plays_nowhere():
    # app da chamada só captura (ainda não tocou nada): não dá para dizer que a saída está errada
    graph = pwgraph.parse(load("pwdump_call_firefox.json"))
    apps = [CallApp("Firefox", FIREFOX_PID, set())]
    assert pwgraph.pick_pc_target(graph, apps, fixed_sink=HDMI, follow=True) == Target(
        HDMI, None, False
    )


def test_pick_target_falls_back_to_default():
    graph = pwgraph.parse(load("pwdump_idle.json"))
    assert pwgraph.pick_pc_target(graph, [], fixed_sink=None, follow=True) == Target(
        ANALOG, None, False
    )
    call = pwgraph.parse(load("pwdump_call_firefox.json"))
    apps = pwgraph.call_apps(call, own_pids=set())
    assert pwgraph.pick_pc_target(call, apps, fixed_sink=None, follow=False).sink == ANALOG


def test_own_aec_nodes_are_not_call_apps():
    dump = load("pwdump_idle.json")
    dump += [
        # captura do módulo echo-cancel: dono é o servidor PipeWire, outro pid
        stream_node(200, "Stream/Input/Audio", "PipeWire", 777, "whisper_ditado_aec_capture"),
        link(201, 62, 200),
        # app de terceiro capturando a fonte virtual nossa
        stream_node(210, "Stream/Input/Audio", "Zoom", 888, "zoom-in"),
        stream_node(211, "Audio/Source", "", None, "whisper_ditado_aec_source"),
        link(212, 211, 210),
    ]
    dump[-2]["info"]["props"]["media.class"] = "Audio/Source"
    graph = pwgraph.parse(dump)
    assert pwgraph.call_apps(graph, own_pids=set()) == []

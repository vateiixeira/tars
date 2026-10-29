import { describe, expect, it } from "vitest";
import type { MeetingDetail, Segment } from "../api/types";
import { alertLines, exportFileName, formatDuration, playingSegmentId, segmentAt, speakerSamples, transcriptText } from "./meeting";

const speakers = [
  { id: "s1", label: "Você", voice_id: null, is_me: true, color_index: 0 },
  { id: "s2", label: "João", voice_id: "v1", is_me: false, color_index: 1 },
];

describe("meeting helpers", () => {
  it("escolhe até três trechos da pessoa, prioriza falas longas e limita cada um a dez segundos", () => {
    const segment = (id: number, start: number, end: number, speaker = "s2"): Segment => ({
      id, start_s: start, end_s: end, speaker_id: speaker, source: "pc", text: "fala", dictated: false,
    });
    const segments = [
      segment(1, 0, 30, "s1"), segment(2, 20, 20.3), segment(3, 30, 50),
      segment(4, 32, 42), segment(5, 60, 67), segment(6, 80, 86),
      segment(7, -1, 10), segment(8, 10, 10), segment(9, 12, NaN),
    ];
    const samples = speakerSamples(segments, "s2");
    expect(samples.map((s) => [s.id, s.start_s, s.end_s])).toEqual([[3, 30, 40], [5, 60, 67], [6, 80, 86]]);
    expect(segments[2].end_s).toBe(50); // os tempos originais são preservados
    expect(speakerSamples(segments, "ausente")).toEqual([]);
  });

  it("transcriptText follows the backend format", () => {
    const text = transcriptText(
      [
        { id: 1, speaker_id: "s1", source: "mic", start_s: 12.4, end_s: 20, text: "Bom dia", dictated: false },
        { id: 2, speaker_id: "s2", source: "pc", start_s: 3725, end_s: 3730, text: "Oi", dictated: true },
        { id: 3, speaker_id: null, source: "pc", start_s: 5, end_s: 6, text: "Hã", dictated: false },
      ],
      speakers,
    );
    expect(text).toBe("[00:12] Você: Bom dia\n[1:02:05] João: Oi (ditado)\n[00:05] Desconhecido: Hã");
  });

  it("exportFileName accepts the zip extension", () => {
    expect(exportFileName("2026-10-19T14:00:00", "zip")).toBe("reuniao-20261019-1400.zip");
  });

  it("segmentAt returns the last segment starting at or before the instant, else the first", () => {
    const segs = [10, 20, 30].map((start_s, id) => ({ id, start_s, end_s: start_s + 5 })) as unknown as Segment[];
    expect(segmentAt(segs, 25)?.id).toBe(1);
    expect(segmentAt(segs, 20)?.id).toBe(1);
    expect(segmentAt(segs, 99)?.id).toBe(2);
    expect(segmentAt(segs, 2)?.id).toBe(0);
    expect(segmentAt([], 5)).toBeUndefined();
  });

  it("exportFileName uses the local start time", () => {
    expect(exportFileName("2026-10-19T14:00:00")).toBe("reuniao-20261019-1400.md");
  });

  it("formatDuration is short and readable", () => {
    expect(formatDuration(20)).toBe("< 1 min");
    expect(formatDuration(42 * 60 + 10)).toBe("42 min");
    expect(formatDuration(3600 + 5 * 60)).toBe("1 h 05 min");
  });

  it("alertLines turns offsets into time of day", () => {
    const m = { started_at: "2026-10-19T12:00:00", alerts: [
      { kind: "mic_silent", start_s: 600, end_s: 2400 },
      { kind: "pc_silent", start_s: 3000, end_s: 3300 },
    ] };
    expect(alertLines(m as MeetingDetail)).toEqual([
      "Microfone sem sinal de 12:10 a 12:40",
      "Sem som do PC de 12:50 a 12:55",
    ]);
  });

  it("labels diarization alerts without a time range", () => {
    const m = { started_at: "2026-10-19T12:00:00", alerts: [
      { kind: "pyannote_no_token", start_s: 0, end_s: 1800 },
    ] };
    expect(alertLines(m as MeetingDetail)).toEqual([
      "pyannote não usado: falta o token do Hugging Face. Falantes separados pelo motor local",
    ]);
  });

  it("playingSegmentId acha a fala tocando: a última iniciada, e nenhuma depois do fim da última", () => {
    const seg = (id: number): Segment => ({
      id, speaker_id: "s1", source: "mic", start_s: id * 3, end_s: id * 3 + 2, text: "x", dictated: false,
    });
    const segs = [seg(0), seg(1), seg(2)]; // 0-2, 3-5, 6-8
    expect(playingSegmentId(segs, 0)).toBe(0);
    expect(playingSegmentId(segs, 4)).toBe(1);
    expect(playingSegmentId(segs, 5.5)).toBe(1); // entre falas: fica na última iniciada
    expect(playingSegmentId(segs, 7)).toBe(2);
    expect(playingSegmentId(segs, 8)).toBeNull(); // fim da última fala
    expect(playingSegmentId(segs, -1)).toBeNull();
    expect(playingSegmentId(segs, null)).toBeNull();
    expect(playingSegmentId(segs, Number.NaN)).toBeNull();
    expect(playingSegmentId([], 1)).toBeNull();
  });
});

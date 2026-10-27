import { describe, expect, it, vi } from "vitest";
import { ApiError, createClient } from "./client";
import type { ApiInfo } from "./types";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("client", () => {
  it("pede o trecho da trilha selecionada com autenticação", async () => {
    const fetchFn = vi.fn(async () => new Response(new Blob(["audio"])));
    const client = createClient(async () => ({ port: 5, token: "tok" }), fetchFn);
    await client.audioBlob("m1", 31, 41, "pc");
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:5/api/meetings/m1/audio?start=31&end=41&source=pc");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("print routes", async () => {
    const fetchFn = vi.fn(async (_u: string, _i?: RequestInit) => new Response(new Blob(["img"])));
    const client = createClient(async () => ({ port: 5, token: "tok" }), fetchFn as unknown as typeof fetch);
    await client.printBlob("m1", "p1", "thumb");
    await client.exportZip("m1");
    expect(fetchFn.mock.calls[0][0]).toBe("http://127.0.0.1:5/api/meetings/m1/prints/p1?size=thumb");
    expect(fetchFn.mock.calls[1][0]).toBe("http://127.0.0.1:5/api/meetings/m1/export.zip");
    fetchFn.mockImplementation(async () => new Response(JSON.stringify({ ok: true })));
    await client.deletePrint("m1", "p1");
    await client.takePrint();
    expect(fetchFn.mock.calls[2][0]).toBe("http://127.0.0.1:5/api/meetings/m1/prints/p1");
    expect(fetchFn.mock.calls[2][1]?.method).toBe("DELETE");
    expect(fetchFn.mock.calls[3][0]).toBe("http://127.0.0.1:5/api/meetings/print");
    expect(fetchFn.mock.calls[3][1]?.method).toBe("POST");
  });

  it("exportZip returns bytes", async () => {
    const fetchFn = vi.fn(async () => new Response(new Uint8Array([80, 75, 3, 4])));
    const client = createClient(async () => ({ port: 5, token: "tok" }), fetchFn);
    expect(Array.from(await client.exportZip("m1"))).toEqual([80, 75, 3, 4]);
  });

  it("client sends bearer token and parses json", async () => {
    const fetchFn = vi.fn(async () =>
      jsonResponse([{ id: "m1", title: "Daily", started_at: "2026-10-19T10:00:00",
                      duration_s: 60, speakers_count: 2, status: "ready" }]),
    );
    const client = createClient(async () => ({ port: 47399, token: "tok" }), fetchFn);

    const items = await client.listMeetings("daily");

    expect(items[0].title).toBe("Daily");
    const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:47399/api/meetings?q=daily");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
    expect(init.method).toBe("GET");
  });

  it("client turns backend detail into ApiError message", async () => {
    const fetchFn = vi.fn(async () => jsonResponse({ detail: "Reunião não encontrada" }, 404));
    const client = createClient(async () => ({ port: 1, token: "t" }), fetchFn);

    const err = await client.getMeeting("x").catch((e) => e);

    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.message).toBe("Reunião não encontrada");
  });

  it("client rereads api info once on 401", async () => {
    const infos: ApiInfo[] = [{ port: 1, token: "velho" }, { port: 2, token: "novo" },
                              { port: 3, token: "outro" }];
    const getInfo = vi.fn(async () => infos.shift()!);
    const fetchFn = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "Token inválido ou ausente" }, 401))
      .mockResolvedValueOnce(jsonResponse({ meeting_id: "m1" }))
      .mockResolvedValueOnce(jsonResponse({ detail: "Token inválido ou ausente" }, 401))
      .mockResolvedValueOnce(jsonResponse({ detail: "Token inválido ou ausente" }, 401));
    const client = createClient(getInfo, fetchFn);

    expect(await client.startMeeting()).toEqual({ meeting_id: "m1" });
    expect(getInfo).toHaveBeenCalledTimes(2);
    const [url, init] = fetchFn.mock.calls[1] as [string, RequestInit];
    expect(url).toBe("http://127.0.0.1:2/api/meetings/start");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer novo");

    // segundo 401 seguido: relê só uma vez e desiste com a mensagem do backend
    const err = await client.status().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(401);
    expect(err.message).toBe("Token inválido ou ausente");
    expect(getInfo).toHaveBeenCalledTimes(3);
  });

  describe("tarefas", () => {
    async function call(run: (c: ReturnType<typeof createClient>) => Promise<unknown>, body: unknown = { ok: true }) {
      const fetchFn = vi.fn(async () => jsonResponse(body));
      const client = createClient(async () => ({ port: 5, token: "t" }), fetchFn);
      const result = await run(client);
      const [url, init] = fetchFn.mock.calls[0] as unknown as [string, RequestInit];
      return { result, url: url.replace("http://127.0.0.1:5/api", ""), init };
    }

    const item = {
      id: "t1", kind: "tarefa" as const, title: "X", description: "", project: null,
      product_id: 1, assignee_id: 2, team_id: 3, stage_id: 4, priority: "low" as const,
    };

    it("listTasks e scanTasks", async () => {
      let r = await call((c) => c.listTasks("m1"));
      expect([r.init.method, r.url, r.init.body]).toEqual(["GET", "/meetings/m1/tasks", undefined]);
      r = await call((c) => c.scanTasks("m1"), { state: "running" });
      expect([r.init.method, r.url, r.init.body]).toEqual(["POST", "/meetings/m1/tasks/scan", undefined]);
      expect(r.result).toEqual({ state: "running" });
      r = await call((c) => c.scanTasks("m1", "  Foque no checkout.  "), { state: "running" });
      expect([r.init.method, r.url, JSON.parse(r.init.body as string)]).toEqual([
        "POST", "/meetings/m1/tasks/scan", { context: "Foque no checkout." },
      ]);
      r = await call((c) => c.scanTasks("m1", " \n "));
      expect(r.init.body).toBeUndefined();
    });

    it("addTask e updateTask", async () => {
      let r = await call((c) => c.addTask("m1", { title: "Novo" }));
      expect([r.init.method, r.url, r.init.body]).toEqual(["POST", "/meetings/m1/tasks", '{"title":"Novo"}']);
      r = await call((c) => c.updateTask("t1", { discarded: true, title: "Y" }));
      expect([r.init.method, r.url, r.init.body]).toEqual([
        "PATCH", "/tasks/t1", '{"discarded":true,"title":"Y"}',
      ]);
    });

    it("cancelTasksScan cancela somente o levantamento da reunião indicada", async () => {
      const scan = { state: "cancelled", error: null, updated_at: "x" };
      const r = await call((c) => c.cancelTasksScan("m/1"), { scan });
      expect([r.init.method, r.url, r.init.body]).toEqual([
        "POST", "/meetings/m%2F1/tasks/scan/cancel", undefined,
      ]);
      expect(r.result).toEqual({ scan });
    });

    it("createTasks e resolveTask", async () => {
      let r = await call((c) => c.createTasks("m1", [item]), { accepted: ["t1"], rejected: [] });
      expect([r.init.method, r.url, r.init.body]).toEqual([
        "POST", "/meetings/m1/tasks/create", JSON.stringify({ items: [item] }),
      ]);
      expect(r.result).toEqual({ accepted: ["t1"], rejected: [] });
      r = await call((c) => c.resolveTask("t1", { action: "retry" }));
      expect([r.init.method, r.url, r.init.body]).toEqual(["POST", "/tasks/t1/resolve", '{"action":"retry"}']);
      r = await call((c) => c.resolveTask("t1", { action: "number", number: 217 }));
      expect(r.init.body).toBe('{"action":"number","number":217}');
    });

    it("issueOptions e issueTest", async () => {
      let r = await call((c) => c.issueOptions(true));
      expect([r.init.method, r.url]).toEqual(["GET", "/issues/options?refresh=true"]);
      r = await call((c) => c.issueOptions());
      expect(r.url).toBe("/issues/options?refresh=false");
      r = await call((c) => c.issueTest(), { ok: true, username: "vini" });
      expect([r.init.method, r.url, r.init.body]).toEqual(["POST", "/issues/test", undefined]);
      expect(r.result).toEqual({ ok: true, username: "vini" });
    });

    it("claudeModels e refreshClaudeModels", async () => {
      let r = await call((c) => c.claudeModels());
      expect([r.init.method, r.url, r.init.body]).toEqual(["GET", "/claude/models", undefined]);
      r = await call((c) => c.refreshClaudeModels());
      expect([r.init.method, r.url, r.init.body]).toEqual(["POST", "/claude/models/refresh", undefined]);
    });
  });
});

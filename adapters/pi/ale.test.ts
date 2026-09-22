import { describe, expect, test } from "bun:test";
import { buildHookDocument, isExecutorEnvironment, mapToolName, rememberToolInput, takeToolInput, translateHookResult } from "./ale";

describe("Pi ALE helpers", () => {
	test("maps edit", () => expect(mapToolName("edit")).toBe("Edit"));
	test("maps write", () => expect(mapToolName("write")).toBe("Write"));
	test("maps bash", () => expect(mapToolName("bash")).toBe("Bash"));
	test("maps read", () => expect(mapToolName("read")).toBe("Read"));
	test("maps grep", () => expect(mapToolName("grep")).toBe("Grep"));
	test("maps find to Glob", () => expect(mapToolName("find")).toBe("Glob"));
	test("maps ls to LS", () => expect(mapToolName("ls")).toBe("LS"));
	test("preserves unknown tool names", () => expect(mapToolName("custom")).toBe("custom"));
	test("translates path to Claude file_path", () => {
		const doc = buildHookDocument("PreToolUse", { sessionId: "s", cwd: "/work", toolName: "edit", toolInput: { path: "a.ts", content: "x" } });
		expect(doc.tool_input).toEqual({ content: "x", file_path: "a.ts" });
	});
	test("includes command input for bash", () => {
		const doc = buildHookDocument("PreToolUse", { sessionId: "s", cwd: "/work", toolName: "bash", toolInput: { command: "pwd" } });
		expect(doc.tool_input).toEqual({ command: "pwd" });
	});
	test("maps Pi tool name in the stdin document", () => {
		const doc = buildHookDocument("PreToolUse", { sessionId: "s", cwd: "/work", toolName: "write", toolInput: { path: "a" } });
		expect(doc.tool_name).toBe("Write");
	});
	test("uses null transcript path by default", () => {
		expect(buildHookDocument("SessionStart", { sessionId: "s", cwd: "/work" }).transcript_path).toBeNull();
	});
	test("exit 2 blocks with stderr", () => {
		expect(translateHookResult("pre-tool", 2, "no writes\n")).toEqual({ block: true, reason: "no writes" });
	});
	test("pre-tool internal failure blocks", () => {
		expect(translateHookResult("pre-tool", 1, "")).toEqual({ block: true, reason: "ale hook failed" });
	});
	test("post-tool internal failure is ignored", () => expect(translateHookResult("post-tool", 1, "oops")).toBeUndefined());
	test("session-start internal failure is ignored", () => expect(translateHookResult("session-start", 1, "oops")).toBeUndefined());
	test("missing ALE_TASK disables registration", () => expect(isExecutorEnvironment({ ALE_AGENT: "a" })).toBe(false));
	test("ALE_TASK enables registration", () => expect(isExecutorEnvironment({ ALE_TASK: "t" })).toBe(true));
	test("ALE_READ_ONLY enables monitor hook registration", () => expect(isExecutorEnvironment({ ALE_READ_ONLY: "1" })).toBe(true));
	test("remembers input for a tool call", () => {
		const inputs = rememberToolInput(new Map(), "call-1", { path: "a.ts" });
		expect(takeToolInput(inputs, "call-1").input).toEqual({ path: "a.ts" });
	});
	test("removes input after taking it", () => {
		const inputs = rememberToolInput(new Map(), "call-1", { path: "a.ts" });
		const taken = takeToolInput(inputs, "call-1");
		expect(taken.inputs.has("call-1")).toBe(false);
	});
	test("unknown tool call ids yield empty input", () => {
		const taken = takeToolInput(new Map(), "missing");
		expect(taken.input).toEqual({});
	});
});

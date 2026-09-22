import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import type { ExtensionAPI, ToolCallEvent } from "@earendil-works/pi-coding-agent";

export const TOOL_NAME_MAP: Readonly<Record<string, string>> = {
	edit: "Edit",
	write: "Write",
	bash: "Bash",
	read: "Read",
	grep: "Grep",
	find: "Glob",
	ls: "LS",
};

export type HookDecision = { block: true; reason: string; terminate?: boolean } | undefined;

export interface HookDocumentOptions {
	sessionId: string;
	cwd: string;
	transcriptPath?: string | null;
	toolName?: string;
	toolInput?: Record<string, unknown>;
}

export function mapToolName(toolName: string): string {
	return TOOL_NAME_MAP[toolName] ?? toolName;
}

export function buildHookDocument(event: string, options: HookDocumentOptions): Record<string, unknown> {
	const input = options.toolInput ?? {};
	const path = typeof input.path === "string" ? input.path : input.file_path;
	const toolInput = { ...input } as Record<string, unknown>;
	delete toolInput.path;
	if (typeof path === "string") toolInput.file_path = path;

	return {
		session_id: options.sessionId,
		cwd: options.cwd,
		hook_event_name: event,
		tool_name: options.toolName ? mapToolName(options.toolName) : "",
		tool_input: toolInput,
		transcript_path: options.transcriptPath ?? null,
	};
}

export function translateHookResult(event: string, code: number, stderr: string): HookDecision {
	if (code === 2) return { block: true, reason: stderr.trim() || "ale hook denied the operation" };
	if (code !== 0 && event === "pre-tool") return { block: true, reason: "ale hook failed" };
	return undefined;
}

export function isExecutorEnvironment(env: NodeJS.ProcessEnv = process.env): boolean {
	return Boolean(env.ALE_TASK || env.ALE_READ_ONLY === "1");
}

export function rememberToolInput(
	inputs: ReadonlyMap<string, Record<string, unknown>>,
	toolCallId: string,
	input: Record<string, unknown>,
): Map<string, Record<string, unknown>> {
	const next = new Map(inputs);
	next.set(toolCallId, input);
	return next;
}

export function takeToolInput(
	inputs: ReadonlyMap<string, Record<string, unknown>>,
	toolCallId: string,
): { input: Record<string, unknown>; inputs: Map<string, Record<string, unknown>> } {
	const next = new Map(inputs);
	const input = next.get(toolCallId) ?? {};
	next.delete(toolCallId);
	return { input, inputs: next };
}

function splitCommand(command: string): [string, string[]] {
	const parts = command.trim().split(/\s+/).filter(Boolean);
	return [parts[0] ?? "python3", parts.slice(1)];
}

interface HookResult {
	code: number;
	stdout: string;
	stderr: string;
}

function runWithStdin(command: string, args: string[], input: string, cwd: string): Promise<HookResult> {
	return new Promise((resolve, reject) => {
		const child = spawn(command, args, { cwd, env: process.env, stdio: ["pipe", "pipe", "pipe"] });
		let stdout = "";
		let stderr = "";
		child.stdout.setEncoding("utf8");
		child.stderr.setEncoding("utf8");
		child.stdout.on("data", (chunk: string) => (stdout += chunk));
		child.stderr.on("data", (chunk: string) => (stderr += chunk));
		child.once("error", reject);
		child.once("close", (code) => resolve({ code: code ?? 1, stdout, stderr }));
		child.stdin.end(input);
	});
}

async function runHook(
	command: string,
	event: string,
	document: Record<string, unknown>,
	cwd: string,
): Promise<HookResult> {
	const [program, args] = splitCommand(command);
	return runWithStdin(program, [...args, "hook", event], `${JSON.stringify(document)}\n`, cwd);
}

function inputForTool(event: ToolCallEvent): Record<string, unknown> {
	return event.input as unknown as Record<string, unknown>;
}

export default function aleExtension(pi: ExtensionAPI): void {
	const env = process.env;
	if (!isExecutorEnvironment(env)) return;

	const task = env.ALE_TASK as string;
	const agent = env.ALE_AGENT ?? "unknown";
	const runDir = env.ALE_RUN_DIR;
	const roster = env.ALE_ROSTER;
	const command = env.ALE_BIN ?? "python3 -m ale";
	const sessionId = randomUUID();
	let cwd = process.cwd();
	let lostClaim = false;
	let contextInjected = false;
	let sessionStartContext = "";
	let inputTokens = 0;
	let outputTokens = 0;
	let toolInputs = new Map<string, Record<string, unknown>>();

	pi.on("session_start", async (_event, ctx) => {
		try {
			cwd = ctx.cwd;
			const result = await runHook(command, "session-start", buildHookDocument("SessionStart", { sessionId, cwd }), cwd);
			if (result.code === 2) lostClaim = true;
			if (result.code === 0) sessionStartContext = result.stdout.trim();
		} catch {
			lostClaim = true;
		}
	});

	pi.on("before_agent_start", async (_event, _ctx) => {
		try {
			if (contextInjected) return undefined;
			contextInjected = true;
			return sessionStartContext ? { systemPrompt: `${_event.systemPrompt}\n\n${sessionStartContext}` } : undefined;
		} catch {
			return undefined;
		}
	});

	pi.on("tool_call", async (event, ctx) => {
		try {
			if (lostClaim) return { block: true, reason: "ALE executor claim was lost", terminate: true };
			const result = await runHook(
				command,
				"pre-tool",
				buildHookDocument("PreToolUse", { sessionId, cwd: ctx.cwd, toolName: event.toolName, toolInput: inputForTool(event) }),
				ctx.cwd,
			);
			const decision = translateHookResult("pre-tool", result.code, result.stderr);
			if (!decision) toolInputs = rememberToolInput(toolInputs, event.toolCallId, inputForTool(event));
			return decision;
		} catch {
			return { block: true, reason: "ale hook failed" };
		}
	});

	pi.on("tool_execution_end", async (event, ctx) => {
		try {
			const taken = takeToolInput(toolInputs, event.toolCallId);
			toolInputs = taken.inputs;
			await runHook(
				command,
				"post-tool",
				buildHookDocument("PostToolUse", { sessionId, cwd: ctx.cwd, toolName: event.toolName, toolInput: taken.input }),
				ctx.cwd,
			);
		} catch {
			// Post-tool failures are intentionally ignored by the protocol.
		}
	});

	pi.on("message_end", async (event) => {
		const usage = (event.message as { role?: string; usage?: { input?: number; output?: number } }).usage;
		if (event.message.role === "assistant" && usage) {
			inputTokens += usage.input ?? 0;
			outputTokens += usage.output ?? 0;
		}
	});

	pi.on("agent_settled", async (_event, ctx) => {
		try {
			await runHook(command, "stop", buildHookDocument("Stop", { sessionId, cwd: ctx.cwd }), ctx.cwd);
			const [program, args] = splitCommand(command);
			if (task) {
				const usageArgs = [
					...args,
					"usage",
					"--task",
					task,
					"--agent",
					agent,
					"--model",
					ctx.model?.id ?? "unknown",
					"--input-tokens",
					String(inputTokens),
					"--output-tokens",
					String(outputTokens),
					"--source",
					"adapter",
				];
				await pi.exec(program, usageArgs, { cwd: runDir ?? ctx.cwd });
			}
		} catch {
			// Cleanup/reporting must not worsen the agent session.
		}
		void roster;
	});
}

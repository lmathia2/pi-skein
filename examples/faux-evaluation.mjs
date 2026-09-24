import { mkdtempSync, rmSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fauxAssistantMessage, fauxToolCall, registerFauxProvider, streamSimple } from "@earendil-works/pi-ai/compat";
import {
  createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager,
} from "@earendil-works/pi-coding-agent";
import { createSkeinExtension } from "../src/index.ts";
import { getSkeinTaskPointer, readSkeinEvidence, readPiTrace } from "../src/evidence.ts";

const workspace = mkdtempSync(join(tmpdir(), "pi-skein-faux-"));
const stateDir = join(workspace, "skein-state");
const faux = registerFauxProvider();
const failing = process.env.SKEIN_FAUX_FAIL === "1";
faux.setResponses(failing
  ? [fauxAssistantMessage("Done."), fauxAssistantMessage("Done."), fauxAssistantMessage("Done.")]
  : [(context) => {
      const sent = JSON.stringify(context.messages);
      if (sent.includes("## TASK") || sent.includes("Workspace changes:")) {
        throw new Error("Pi model request received an unexpected Skein task packet");
      }
      return fauxAssistantMessage(fauxToolCall("code", { code: "write('result.txt', 'ok')" }), { stopReason: "toolUse" });
    },
    (context) => {
      const sent = JSON.stringify(context.messages);
      if (!sent.includes("result.txt")) {
        throw new Error("Pi continuation lacks the native tool result");
      }
      return fauxAssistantMessage("The file is ready.");
    }]);
const model = faux.getModel();
const modelRuntime = await ModelRuntime.create({ refreshOnCreate: false });
modelRuntime.registerProvider(model.provider, {
  baseUrl: model.baseUrl,
  apiKey: "faux-key",
  api: faux.api,
  models: faux.models,
  streamSimple,
});
const extension = createSkeinExtension({
  task: { goal: "Create result.txt", criteria: ["result.txt contains ok"],
    verification_requirements: ["test \"$(cat result.txt)\" = ok"] },
  stateDir,
  python: process.env.PI_SKEIN_PYTHON || "python3",
});
const settingsManager = SettingsManager.inMemory({ retry: { enabled: false } });
const resourceLoader = new DefaultResourceLoader({
  cwd: workspace, agentDir: join(workspace, "agent"), settingsManager,
  extensionFactories: [extension], noExtensions: true,
});
await resourceLoader.reload();
const { session } = await createAgentSession({
  cwd: workspace, agentDir: join(workspace, "agent"), model, modelRuntime,
  settingsManager, resourceLoader, sessionManager: SessionManager.inMemory(workspace),
});
try {
  await session.bindExtensions({});
  await session.prompt("Create result.txt containing ok");
  const pointer = getSkeinTaskPointer(session.sessionManager.getBranch());
  if (!pointer) throw new Error("missing Skein task pointer");
  const evidence = readSkeinEvidence(stateDir, pointer.taskId);
  const events = readFileSync(evidence.tracePath, "utf8").trim().split("\n").map((line) => JSON.parse(line));
  const piEvents = readPiTrace(stateDir, session.sessionManager.getSessionId()).events;
  if (!piEvents.some((event) => event.kind === "pi.context_prepared") ||
      (!failing && !piEvents.some((event) => event.kind === "pi.tool_completed" && event.payload.tool_name === "code"))) {
    throw new Error("Pi observation trace did not capture the model context and code result");
  }
  if (!events.some((event) => event.kind === "pi.context_prepared") ||
      (!failing && !events.some((event) => event.kind === "result.retained"))) {
    throw new Error("Pi model context was not linked to the durable Skein trace");
  }
  if (failing && evidence.outcome?.status !== "blocked") {
    throw new Error(`failed verification did not block: ${JSON.stringify(evidence)}`);
  }
  if (!failing && (evidence.outcome?.status !== "complete" || !evidence.verification?.passed)) {
    throw new Error(`unverified outcome: ${JSON.stringify(evidence)}`);
  }
  console.log(failing ? "Pi stopped after bounded failed verification" :
    "Pi completed a faux-provider coding run with Skein host verification");
} finally {
  session.dispose();
  extension.dispose();
  faux.unregister();
  rmSync(workspace, { recursive: true, force: true });
}

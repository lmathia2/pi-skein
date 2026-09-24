import { createHash } from 'node:crypto';
import { existsSync, mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fauxAssistantMessage, fauxToolCall, registerFauxProvider, streamSimple } from '@earendil-works/pi-ai/compat';
import { createAgentSession, DefaultResourceLoader, ModelRuntime, SessionManager, SettingsManager } from '@earendil-works/pi-coding-agent';
import { createSkeinExtension } from '../src/index.ts';
import { getSkeinTaskPointer, readSkeinEvidence, readPiTrace, readPiTraceArtifact } from '../src/evidence.ts';

const workspace = mkdtempSync(join(tmpdir(), 'pi-skein-modes-'));
const stateDir = join(workspace, 'skein-state');
const defaults = ['read', 'bash', 'edit', 'write'];

async function trial(name, settings) {
  const faux = registerFauxProvider();
  let firstContext;
  faux.setResponses([
    (context) => {
      firstContext = context;
      return fauxAssistantMessage(fauxToolCall('bash', { command: 'printf native-pi' }), { stopReason: 'toolUse' });
    },
    fauxAssistantMessage('Done.'),
  ]);
  const model = faux.getModel();
  const modelRuntime = await ModelRuntime.create({ refreshOnCreate: false });
  modelRuntime.registerProvider(model.provider, { baseUrl: model.baseUrl, apiKey: 'faux-key',
    api: faux.api, models: faux.models, streamSimple });
  const extension = settings === null ? null : createSkeinExtension({ ...settings, stateDir,
    python: process.env.PI_SKEIN_PYTHON || 'python3' });
  const settingsManager = SettingsManager.inMemory({ retry: { enabled: false } });
  const resourceLoader = new DefaultResourceLoader({ cwd: workspace, agentDir: join(workspace, `agent-${name}`),
    settingsManager, extensionFactories: extension ? [extension] : [], noExtensions: true });
  await resourceLoader.reload();
  const { session } = await createAgentSession({ cwd: workspace, agentDir: join(workspace, `agent-${name}`),
    model, modelRuntime, settingsManager, resourceLoader, sessionManager: SessionManager.inMemory(workspace) });
  try {
    await session.bindExtensions({});
    const tools = session.getActiveToolNames();
    if (tools.join(',') !== defaults.join(',')) throw new Error(`${name}: tools ${tools}`);
    await session.prompt('Print native-pi using the shell');
    const system = firstContext?.messages.find((message) => message.role === 'system')?.sections;
    if (!system) throw new Error(`${name}: no provider system sections`);
    const sessionId = session.sessionManager.getSessionId();
    const tracePath = join(stateDir, 'pi-trace', createHash('sha256').update(sessionId).digest('hex'), 'events.jsonl');
    if (getSkeinTaskPointer(session.sessionManager.getBranch())) throw new Error(`${name}: native mode opened a PTC task`);
    return { system, tools, tracePath, sessionId };
  } finally {
    session.dispose(); extension?.dispose(); faux.unregister();
  }
}

async function codeTrial(contract) {
  const name = contract ? 'code-contract' : 'code-minimal';
  const faux = registerFauxProvider();
  let modelSystem;
  faux.setResponses([
    (context) => {
      modelSystem = context.messages.find((message) => message.role === 'system')?.sections;
      return fauxAssistantMessage(fauxToolCall('code', { code: "print('persistent-python')" }), { stopReason: 'toolUse' });
    },
    fauxAssistantMessage('Done.'),
  ]);
  const model = faux.getModel();
  const modelRuntime = await ModelRuntime.create({ refreshOnCreate: false });
  modelRuntime.registerProvider(model.provider, { baseUrl: model.baseUrl, apiKey: 'faux-key',
    api: faux.api, models: faux.models, streamSimple });
  const extension = createSkeinExtension({ codeMode: true, traceExecution: false, modelContract: contract,
    stateDir, python: process.env.PI_SKEIN_PYTHON || 'python3' });
  const settingsManager = SettingsManager.inMemory({ retry: { enabled: false } });
  const resourceLoader = new DefaultResourceLoader({ cwd: workspace, agentDir: join(workspace, `agent-${name}`),
    settingsManager, extensionFactories: [extension], noExtensions: true });
  await resourceLoader.reload();
  const { session } = await createAgentSession({ cwd: workspace, agentDir: join(workspace, `agent-${name}`),
    model, modelRuntime, settingsManager, resourceLoader, sessionManager: SessionManager.inMemory(workspace) });
  try {
    await session.bindExtensions({});
    if (session.getActiveToolNames().join(',') !== 'code') throw new Error(`${name}: code tool not exclusive`);
    const description = session.getAllTools().find((tool) => tool.name === 'code')?.description;
    if (contract ? !description?.includes('Prefer one code call') : description?.includes('Prefer one code call')) {
      throw new Error(`${name}: incorrect model-facing contract`);
    }
    await session.prompt('Print persistent-python');
    const pointer = getSkeinTaskPointer(session.sessionManager.getBranch());
    if (!pointer) throw new Error(`${name}: missing PTC task journal`);
    const evidence = readSkeinEvidence(stateDir, pointer.taskId);
    const events = readFileSync(evidence.tracePath, 'utf8').trim().split('\n').map(JSON.parse);
    if (!events.some((event) => event.kind === 'cell.completed')) throw new Error(`${name}: cell not journaled`);
    const tracePath = join(stateDir, 'pi-trace', createHash('sha256').update(session.sessionManager.getSessionId()).digest('hex'), 'events.jsonl');
    if (existsSync(tracePath)) throw new Error(`${name}: trace switch off wrote Pi-native trace`);
    await session.prompt('/skein code off');
    if (session.getActiveToolNames().join(',') !== defaults.join(',')) throw new Error(`${name}: code off did not restore Pi tools`);
    await session.prompt('/skein trace on');
    if (!existsSync(tracePath)) throw new Error(`${name}: trace on did not start Pi trace`);
    await session.prompt(`/skein contract ${contract ? 'off' : 'on'}`);
    const toggled = session.getAllTools().find((tool) => tool.name === 'code')?.description;
    if (contract ? toggled?.includes('Prefer one code call') : !toggled?.includes('Prefer one code call')) {
      throw new Error(`${name}: contract toggle did not update the tool definition`);
    }
    await session.prompt('/skein trace off');
    await session.prompt('/skein code on');
    if (session.getActiveToolNames().join(',') !== 'code') throw new Error(`${name}: code on did not select code`);
    return modelSystem;
  } finally {
    session.dispose(); extension.dispose(); faux.unregister();
  }
}

try {
  const baseline = await trial('baseline', null);
  const disabled = await trial('disabled', { codeMode: false, traceExecution: false });
  if (JSON.stringify(disabled.system) !== JSON.stringify(baseline.system)) {
    throw new Error('both-off provider system context differs from default Pi');
  }
  if (existsSync(disabled.tracePath)) throw new Error('both-off unexpectedly wrote a Pi trace');
  const observed = await trial('observed', { codeMode: false, traceExecution: true });
  if (JSON.stringify(observed.system) !== JSON.stringify(baseline.system)) {
    throw new Error('trace-only provider system context differs from default Pi');
  }
  if (!existsSync(observed.tracePath)) throw new Error('trace-only missed the native Pi trace');
  const events = readPiTrace(stateDir, observed.sessionId).events;
  for (const kind of ['pi.context_prepared', 'pi.tool_started', 'pi.tool_completed', 'pi.message_completed']) {
    if (!events.some((event) => event.kind === kind)) throw new Error(`trace-only missing ${kind}`);
  }
  if (!events.some((event) => event.kind === 'pi.tool_completed' && event.payload.tool_name === 'bash')) {
    throw new Error('trace-only missed native Bash completion');
  }
  const contextEvent = events.find((event) => event.kind === 'pi.context_prepared');
  const recordedContext = readPiTraceArtifact(stateDir, observed.sessionId, contextEvent.payload.artifact_uri);
  if (!Array.isArray(recordedContext) || recordedContext[0]?.role !== 'system') {
    throw new Error('trace-only context artifact does not contain the model system message');
  }
  const fullContract = await codeTrial(true);
  const minimalContract = await codeTrial(false);
  if (JSON.stringify(fullContract) === JSON.stringify(minimalContract)) {
    throw new Error('model contract switch did not change the provider-facing system sections');
  }
  console.log('Both-off matches default Pi prompt/tools; trace-only records native Bash without changing them');
  console.log('Code mode keeps its safety journal with Pi trace off; contract switch changes only code instructions');
} finally {
  rmSync(workspace, { recursive: true, force: true });
}

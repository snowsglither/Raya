# RAYA V2 — ARCHITECTURAL BLUEPRINT

**Status:** Architecture specification / migration target  
**Purpose:** Define the architecture, principles, boundaries and migration direction for RAYA V2 before implementation.

---

# 1. Vision

RAYA V2 is not RAYA 1.5 and is not primarily a chatbot with tools.

RAYA V2 is designed as a **persistent AI runtime / digital presence**:

> **A permanent consciousness-like but resource-efficient butler runtime.**

"Consciousness" here is an architectural metaphor. RAYA must NOT require a permanently running large language model. The runtime, state, perception and event handling remain lightweight; expensive reasoning, vision, speech synthesis and other heavy capabilities are activated when useful.

The central principle is:

**The model is not RAYA. The model is one cognitive component inside RAYA.**

RAYA must remain useful if the model provider, interface, or a specific device agent changes.

---

# 2. Core Architectural Thesis

A larger model alone does not create a better agent.

RAYA V2 therefore separates:

- persistent runtime/state
- world state
- memory
- attention
- context engineering
- cognition
- agentic harness
- tasks
- tools
- environment/device agents
- model providers
- interfaces

The architecture follows the principle:

> **Brain and hands are decoupled.**

The model decides what should happen and reasons about novel situations. The runtime/harness manages state, context, execution, verification, cancellation, recovery and tool orchestration. Device agents provide the actual capabilities needed to affect the environment.

---

# 3. Target Architecture

```text
                         INTERFACES
              Voice / Web / Desktop / Mobile / API
                              |
                              v
                         ATTENTION
                 priority / focus / interruption
                              |
                              v
                    AGENTIC HARNESS
                              |
        +---------------------+---------------------+
        |                     |                     |
        v                     v                     v
    COGNITION               TASKS                CONTEXT
    reasoning               actors              engineering
    hypotheses              background          ranking
    uncertainty             persistence         filtering
    options                checkpoints          budgets
    verification            recovery             compaction
        |                     |                     |
        +---------------------+---------------------+
                              |
                              v
                         WORLD STATE
                              |
                 +------------+------------+
                 |                         |
                 v                         v
              MEMORY                  ENVIRONMENT
                                      /    |    \
                                     PC Browser Devices
```

The **Model Layer** is transversal:

```text
                       MODEL LAYER
                            |
          +-----------------+-----------------+
          |                 |                 |
       Reasoning          Vision            Code
          |                 |                 |
       provider            provider          provider
          |                 |                 |
       local/cloud       local/cloud       local/cloud
```

Interfaces are clients of the Core, not the Core itself.

---

# 4. Headless First

The first V2 implementation should be **headless**.

No major UI should be required to validate the architecture.

A minimal CLI/API/test harness is enough initially.

The Core must be able to:

- receive an event/request
- inspect state
- retrieve relevant memory/context
- reason
- create/manage tasks
- discover and execute tools
- verify results
- update World State
- persist state
- recover from interruption/failure

Web, desktop, mobile and voice interfaces come later and must communicate with the Core through stable contracts.

---

# 5. World State

World State represents the current known state of the environment.

It is NOT long-term memory.

Possible domains:

- PC state
- active application
- active window
- processes
- files
- network
- connected devices
- USB devices
- battery/power
- notifications
- browser/page state
- cameras/devices
- running RAYA tasks
- system conditions

Every meaningful observation should support metadata such as:

```text
fact
timestamp
source
confidence
freshness
status
```

Distinguish:

- known fact
- unknown
- inferred state
- hypothesis

World State should be updated by perception and verified actions.

It must not become an uncontrolled permanent event log.

---

# 6. Perception

Perception observes the environment and converts observations into structured events/state updates.

Examples:

- application/window changes
- process changes
- USB connection
- network/device changes
- battery state
- notifications
- task completion/failure
- browser changes
- explicit screen capture
- vision requests

Important rules:

- Do not continuously transcribe the microphone.
- Do not continuously inspect webcam content.
- Do not continuously analyze the screen unless explicitly required.
- Lightweight sensors may run continuously.
- Expensive perception is activated on demand.
- Perception should produce events/state, not directly invoke the LLM for every observation.

Vision is a capability, not a permanent sensor.

---

# 7. Attention

Attention is a new fundamental subsystem.

Its responsibility is deciding:

- what deserves processing
- what deserves LLM reasoning
- what deserves immediate interruption
- what can remain background
- what can be ignored
- which task has focus
- when user conversation overrides background work

Attention should consider:

- urgency
- importance
- user relevance
- novelty
- confidence
- cost
- current focus
- task priority
- interruption policy

Silence is a valid outcome.

RAYA should not narrate every internal action.

---

# 8. Agentic Harness

The Agentic Harness is the central runtime of RAYA V2.

It is NOT simply an LLM wrapper.

Responsibilities include:

- session lifecycle
- durable event/session state
- model invocation
- context assembly
- dynamic tool discovery
- tool execution
- task coordination
- cancellation
- interruption
- checkpointing
- verification
- replanning
- recovery
- persistence
- steering during execution
- state updates
- observability/audit

Conceptually:

```text
Event / Request
      |
      v
Attention
      |
      v
Harness
      |
      +--> retrieve state/memory/context
      |
      +--> model reasoning
      |
      +--> discover capability/tool
      |
      +--> execute
      |
      +--> verify
      |
      +--> update state
      |
      +--> continue / replan / finish
```

The Harness must support long-running agentic work.

The model context must NOT be treated as the sole source of truth.

Persistent execution state belongs outside the model context.

---

# 9. Cognition

Cognition contains concepts such as:

- intent
- reasoning
- uncertainty
- hypotheses
- ambiguity
- option generation
- option comparison
- prioritization
- verification
- self-correction
- recovery

Separate:

**Information**
from
**Objectives**

RAYA should maintain:

- known information
- unknown information
- inferred hypotheses
- confidence

When ambiguity blocks an action, ask the smallest useful question.

When ambiguity does not block safe progress, continue.

Do not store raw chain-of-thought as memory.

Store structured reasoning state instead:

```text
objective
current_state
relevant_information
hypotheses
options
decision
reason
actions
blockers
next_checkpoint
```

Deterministic code should handle facts, constraints, history, verification and execution.

Models should handle novel/open-ended reasoning, interpretation, intent and creative generation.

Hybrid decisions should combine both.

---

# 10. Context Engine

Context Engineering replaces the idea of one giant static system prompt.

The Context Engine dynamically builds the model context from the highest-value information.

It should handle:

- ranking
- filtering
- relevance
- token budgets
- memory retrieval
- World State selection
- task state
- conversation history
- tool schemas
- model-specific context limits
- compaction
- stale information removal
- provenance

Do not inject every memory, tool or event into every model call.

Context should be assembled for the specific task.

---

# 11. Memory

Memory is separate from World State.

Required conceptual layers:

### Working Memory
Temporary active reasoning/session context.

### Conversation Memory
Channel-specific conversation history.

### Personal Memory
Durable user facts/preferences that are appropriate to retain.

### Project Memory
Structured knowledge belonging to a project.

### Task Memory
Reminders, deadlines, commitments and task-related state.

### Experience Memory
Past successes, errors, corrections and lessons.

### General Knowledge
Prefer retrieval/live sources for changing knowledge; stable knowledge can be retained with provenance where useful.

Memory must support:

- confidence
- provenance
- freshness
- lifecycle
- isolation
- correction
- contradiction handling

Suggested lifecycle:

```text
candidate
   ->
active
   ->
confirmed
   ->
aging
   ->
obsolete
```

Memory types should be distinguishable:

- fact
- preference
- rule/policy
- experience

User corrections have high priority.

Channel isolation is strict.

Only explicitly permitted shared information crosses channels.

Do not permanently store:

- conversation noise
- temporary credentials/secrets
- unconfirmed hypotheses as facts
- easily retrievable search results without durable value

---

# 12. Tasks

The old Task Manager concept must be replaced by persistent Task Actors.

A task should have structured state such as:

```text
id
objective
state
priority
context
progress
dependencies
created_at
updated_at
checkpoint
cancellation
result
error
owner
```

Tasks must support:

- background execution
- pause
- resume
- cancellation
- checkpointing
- persistence
- recovery
- dependencies
- priority
- events
- verification

Conversation must remain independent from long-running background tasks.

Example:

```text
Task A: download/process file
Task B: answer user question
Task C: monitor long-running operation
```

RAYA must be able to answer the user while Task A continues.

---

# 13. Tool System

Tools are capabilities, not intelligence.

The tool architecture must provide:

- registry
- capability metadata
- dynamic discovery
- schema
- validation
- permissions
- timeout
- cancellation
- retry
- fallback
- verification
- isolation
- auditability

Do not expose hundreds of tool definitions to the model unnecessarily.

The model/harness should discover the capabilities relevant to the current objective.

Tools should have explicit contracts.

A tool should report structured success/failure and evidence where possible.

---

# 14. Computer Use / PC Agent

The old PC Control Engine must NOT survive as the V2 decision engine.

Rebuild the architecture.

Low-level capabilities may survive as implementation mechanisms:

- UI Automation
- keyboard
- mouse
- CLI/shell
- filesystem
- process management
- window management
- screen capture
- vision

But these are **hands**.

The Core should request capabilities such as:

```text
application.launch
application.close
window.find
file.read
file.write
process.inspect
process.start
input.click
input.type
```

The Windows/PC Agent chooses the appropriate mechanism.

The model should not need to reason in terms of raw coordinates or low-level implementation unless necessary.

Execution must be followed by verification.

---

# 15. Browser Agent

Existing robust browser mechanisms may survive as implementation capabilities:

- CDP
- DOM interaction
- auto-wait
- event-driven waiting
- vision fallback

But the browser agent itself must be rebuilt around the Harness.

Required behavior:

- navigation
- multi-step workflows
- verification
- recovery
- replanning
- cancellation
- interruption
- state awareness

Browser implementation details must remain behind the browser capability interface.

---

# 16. Model Layer / Ollama

RAYA is **Ollama Cloud-first**.

The user's hardware does not need to run the largest model locally.

Ollama Cloud provides access to larger models while local Ollama can remain available for lightweight/fast/offline tasks.

The Core must not depend directly on a specific model.

Create a clean Model Abstraction and Registry.

The Core requests capabilities, for example:

```text
reasoning
planning
coding
vision
fast_response
classification
summarization
```

The Model Router chooses among available models based on factors such as:

- capability
- task
- quality
- context capacity
- latency
- availability
- local/cloud
- VRAM requirements
- cost
- modality

The architecture must support multiple providers/models without changing the Core.

Do not use an Anthropic-shaped internal contract as the permanent RAYA model contract merely because the current implementation migrated through Anthropic-compatible structures.

Create a native RAYA model request/response abstraction.

---

# 17. Learning

Learning is structured experience accumulation, not automatic model retraining.

Store useful:

- corrections
- successful approaches
- failed approaches
- lessons
- preferences
- recurring patterns

Do NOT implement automatic fine-tuning/retraining as part of the initial V2.

Future model adaptation may be added later.

---

# 18. Safety / Autonomy

Safety must be an architectural subsystem.

Required concepts:

- permission policies
- action risk classification
- confirmation
- sandboxing
- override
- STOP
- cancellation
- audit
- rollback where possible
- sensitive-action protection

Not every action requires confirmation.

Safe reversible actions should be low friction.

High-impact/destructive/sensitive actions require stronger permission.

STOP must have priority and must be reliable.

---

# 19. Devices / Agents

The Core should know devices through a Device Registry.

Each device/agent should expose:

```text
identity
status
capabilities
availability
health
permissions
```

Potential agents:

- Windows Agent
- Linux Agent
- iOS Agent
- Browser Agent
- Camera Agent
- future specialized agents

The Core should issue capability-level requests rather than relying on device-specific implementation details.

---

# 20. Interfaces

Interfaces are independent clients.

Initial implementation should not depend on a rich UI.

Potential future interfaces:

- CLI
- Web
- Desktop
- Mobile
- Voice
- API

Voice is a communication channel, not the brain.

---

# 21. Realtime Voice

The old voice runtime should be rebuilt.

Keep the behavioral requirements, not the old implementation.

Target behavior:

- real-time bidirectional audio
- speech detection based on voice characteristics, not volume alone
- pause/intonation/semantic completion
- partial understanding
- early preparation where safe
- no irreversible action from an unconfirmed partial hypothesis
- immediate barge-in
- stop speaking when user starts speaking
- listen while speaking
- own-voice filtering
- cancellation commands
- short spoken responses
- background work while speaking
- interruption-aware task handling
- silence when no response is useful

Control semantics include concepts such as:

- stop speaking
- cancel
- abandon
- continue
- resume

The voice system must connect to Attention + Harness rather than implement its own independent brain.

Voice should be implemented after the core architecture is stable.

---

# 22. Files / Vision / Generation

Existing reliable extraction capabilities can be reused where justified:

- PDF
- DOCX
- XLSX
- CSV
- source code
- text/markdown

Generation must become intent-driven.

"Generate" does not mean "3D/Blender".

Generation may mean:

- image
- document
- code
- presentation
- animation
- 3D
- interface
- simulation
- other artifact

The requested objective determines the capability.

Engineering/manufacturing objects must have explicit constraints such as dimensions, units, tolerances and target device where applicable.

Presentation/holographic/interactive 3D is conceptually different from engineering CAD/manufacturing.

---

# 23. Server / Remote

RAYA should support a central always-on Linux-first runtime.

The central server can host:

- Core
- orchestration
- memory
- API
- device registry
- remote services
- monitoring

Heavy work may execute on device agents.

Cloudflare Tunnel is an acceptable remote connectivity mechanism.

Security requirements include:

- authentication
- TLS
- no unnecessary public ports
- secrets outside source
- monitoring
- explicit device permissions

Network bandwidth governance may be implemented at the application layer, but application limits are not a replacement for router-level QoS when true network shaping is required.

---

# 24. Persistence / Event History

The model context is not the durable source of truth.

The runtime should maintain durable structured state and an event/session history sufficient for:

- recovery
- resumption
- debugging
- task continuation
- auditing

Avoid making the event log an uncontrolled database of everything forever.

Use structured event types and lifecycle/retention rules.

---

# 25. Migration Philosophy

The current RAYA repository is **V1 / raw material**.

Do not preserve components merely because they currently work.

For every existing subsystem, classify it:

```text
KEEP
    The abstraction and implementation are genuinely suitable.

EXTRACT
    A capability is valuable, but its current architecture is not.

ADAPT
    The implementation can be modified without violating V2 boundaries.

REBUILD
    The concept is useful, but the implementation/architecture is incompatible.

DELETE
    Legacy, duplicate, dead, over-coupled or unnecessary.
```

Never blindly move old files into new folders.

Do not create a "new orchestrator.py" that becomes another monolith.

Do not recreate V1 architecture under different names.

---

# 26. Expected V2 Boundaries

The exact module/file structure may evolve, but responsibilities must remain separated.

At minimum the architecture must have clear boundaries for:

```text
runtime
world_state
perception
attention
harness
cognition
context
memory
tasks
tools
models
safety
devices
interfaces
persistence
observability
```

The final structure must prevent one module from becoming a universal dependency.

---

# 27. Migration Order

Recommended order:

## Phase 0 — Architecture Contracts
Define boundaries, interfaces, IDs, events, persistence and schemas.

## Phase 1 — Harness + World State + Memory + Context
Build the central foundation.

## Phase 2 — Attention + Task Actors
Add prioritization, background work, cancellation and concurrency.

## Phase 3 — Cognition + Tool System + Model Layer
Connect reasoning, dynamic capabilities and Ollama routing.

## Phase 4 — Environment Agents
Rebuild PC/Windows, Browser and other device agents.

## Phase 5 — Realtime Voice
Build the voice runtime on top of the stable Core.

## Phase 6 — Interfaces / Remote UX
Build Web/Desktop/Mobile/advanced interfaces as clients.

---

# 28. Testing Philosophy

Testing must prove real behavior, not inflate test counts.

Target approximately **150–200 focused tests** for major V2 validation rather than hundreds of artificial permutations.

Tests should include real integration where practical:

- Agentic Harness
- long-running tasks
- task cancellation
- pause/resume
- recovery
- memory lifecycle
- World State freshness
- channel isolation
- tool discovery
- tool permissions
- tool failure/retry
- model routing
- Ollama Cloud/local integration
- real Windows actions
- real browser interaction
- interruption
- concurrency
- STOP behavior
- persistence/recovery
- device agent communication

Use explicit statuses:

```text
PASS
FAIL
BLOCKED
NOT_TESTED
```

Never claim success when an environment limitation prevented validation.

---

# 29. Definition of Success

RAYA V2 should eventually pass the following conceptual scenario:

> "Download this file, put it into my project, make sure nothing important is overwritten, launch the project, check whether it works, fix it if it fails, and keep doing that in the background while I ask you another question."

Expected behavior:

```text
User request
    |
    v
Attention
    |
    v
Harness creates Task Actor
    |
    +--> file operation
    +--> verification
    +--> project launch
    +--> observe result
    +--> failure
    +--> replan
    +--> correction
    +--> verification
    |
    +--> task continues independently
    |
    +--> user asks another question
             |
             v
        conversation handled
```

The background task must not block normal conversation.

RAYA must know what it has actually verified versus what it merely assumes.

---

# 30. Non-Goals for Initial V2

Do not make these prerequisites for the first functional V2 core:

- rich graphical interface
- full mobile application
- perfect realtime voice
- automatic fine-tuning
- permanent webcam analysis
- permanent screen analysis
- permanent speech transcription
- dozens of device agents
- advanced 3D generation
- manufacturing/CAD automation
- Kubernetes-scale infrastructure

Build the foundation first.

---

# 31. Final Principle

RAYA V2 should be understood as:

```text
             MODEL ≠ RAYA

MODEL
    reasons / interprets / generates options

HARNESS
    manages execution / context / state / tools

MEMORY
    preserves durable knowledge

WORLD STATE
    represents current environment

ATTENTION
    decides what deserves focus

TASKS
    provide persistent autonomous work

TOOLS
    provide capabilities

DEVICE AGENTS
    operate the physical/digital environment

INTERFACES
    communicate with the user
```

The goal is not to make the current RAYA codebase prettier.

The goal is to create a **clean, extensible, model-independent agent runtime** and migrate valuable capabilities from the existing repository into it.

The current repository is evidence and raw material.

The V2 architecture is the target.

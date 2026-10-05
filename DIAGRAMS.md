# How the demo is wired

The TEM Agent runs its optimisation loops with a deterministic policy, so a demo
needs no LLM, no API key and no network call to a model provider. These diagrams
are the wiring and the two workflows the demo offers.

The same Mermaid source sits in `web/index.html` behind a fold, so the page can
be re-rendered wherever Mermaid is available.

## Components

```mermaid
flowchart LR
  UI["web/index.html<br/>browser page"] -->|"POST /api/run<br/>GET /api/state"| SRV["demo_server.py<br/>stdlib HTTP"]
  SRV -->|"invoke_tem_ai_model(model=rule)"| AG["tem_agent.py<br/>run_goal() loop"]
  AG -->|"get_next_action()"| POL["policies/rule_policy.py"]
  POL -->|"tool call"| AG
  AG -->|"execute(name, params)"| CAP["tem_capabilities.py"]
  CAP -.->|"tool schemas"| TOOLS["config/tools.yaml<br/>13 tools"]
  CAP -.->|"safety limits"| LIM["config/instrument_autoscript.yaml"]
  CAP --> CONN["connectors/autoscript_connector.py"]
  CAP -.->|"--dry-run"| MOCK["tests/mock_connector.py<br/>in memory"]
  CONN --> IF["autoscript_interface.py<br/>AutoScript bindings"]
  IF --> CL["TemMicroscopeClient()"]
  CL -->|"framed protocol, TCP 7521"| SIM["AutoScript TEM simulator<br/>13.202.106.11:7521"]
```

Node colour is the same link at two levels of detail:

| Hop | What crosses it |
| --- | --- |
| page to `demo_server.py` | `POST /api/run` with the loop, the dry-run flag and the host, then `GET /api/state` polling while the agent prints |
| `demo_server.py` to `tem_agent.py` | `invoke_tem_ai_model(model_name="rule")` on a background thread, with the agent's stdout captured line by line |
| loop to `rule_policy.py` | `get_next_action()` returns the next tool call; each result returns through `post_tool_results()` |
| loop to `tem_capabilities.py` | the tool call itself, with every argument checked against the instrument YAML before anything reaches the instrument |
| capabilities to connector | one of two connectors: the AutoScript one for hardware, the mock one under `--dry-run` |
| connector to simulator | `autoscript_interface.py` calls `TemMicroscopeClient`, which speaks the AutoScript framed protocol to port 7521. No HTTP anywhere in this hop |

## The focus loop, step by step

```mermaid
sequenceDiagram
  autonumber
  participant UI as web page
  participant SRV as demo_server
  participant AG as TEMAgent loop
  participant POL as rule_policy
  participant CAP as tem_capabilities
  participant SIM as AutoScript
  UI->>SRV: POST /api/run {loop: focus2}
  SRV->>AG: invoke_tem_ai_model(model=rule)
  AG->>CAP: get_microscope_state
  CAP->>SIM: read stage, defocus, optical mode, voltage
  AG->>POL: get_next_action()
  POL-->>AG: set_defocus for sweep point 1
  loop seven coarse points, then five fine
    AG->>CAP: set_defocus(target)
    CAP->>CAP: reject a change over 5 µm
    CAP->>SIM: set the objective defocus
    AG->>CAP: acquire_image
    CAP->>SIM: acquire_stem_image
    CAP-->>AG: normalized variance of the frame
    AG->>POL: post_tool_results
  end
  POL-->>AG: set_defocus(best)
  AG->>CAP: finish(summary)
  CAP->>CAP: require the resting defocus within 10 nm of the best
  AG-->>SRV: stdout lines
  SRV-->>UI: polled state, points, best focus
```

## The tomography loop

```mermaid
flowchart TD
  A["get_microscope_state"] --> B["open_column_valve, unblank_beam,<br/>set_optical_mode, configure_detector"]
  B --> C{"another angle?"}
  C -->|"yes"| D["move_stage(dA = target - current)"]
  D --> E["refocus sweep at this angle<br/>3 points, score each frame"]
  E --> F["set the best defocus"]
  F --> G["acquire the projection image"]
  G --> C
  C -->|"no"| H["move_stage back to 0 degrees"]
  H --> I["set the best defocus of the series"]
  I --> J["finish"]
```

## The policy's own state machine

Each loop is a small state machine. Every `get_next_action()` call returns one
tool call, and the result it gets back decides the next state.

```mermaid
stateDiagram-v2
  [*] --> start
  start --> after_start : get_microscope_state
  after_start --> sweep_set : focus, focus2
  after_start --> survey_acquire : survey
  after_start --> tomo_tilt : tomography
  sweep_set --> sweep_acquire : set_defocus
  sweep_acquire --> sweep_record : acquire_image
  sweep_record --> sweep_set : next point
  sweep_record --> sweep_pick : sweep finished
  sweep_pick --> sweep_set : focus2, stage 2
  sweep_pick --> tune_check : restore the best
  tune_check --> finish
  tomo_tilt --> tomo_tilt_read : move_stage
  tomo_tilt_read --> tomo_sweep_set
  tomo_sweep_set --> tomo_sweep_acquire : set_defocus
  tomo_sweep_acquire --> tomo_sweep_record : acquire_image
  tomo_sweep_record --> tomo_sweep_set
  tomo_sweep_record --> tomo_restore : angle focused
  tomo_restore --> tomo_projection
  tomo_projection --> tomo_tilt
  tomo_tilt --> tomo_restore_read : angles done, stage still tilted
  tomo_restore_read --> tomo_final_restore : set the series best focus
  tomo_final_restore --> finish
  survey_acquire --> survey_done : acquire_image
  survey_done --> finish
  finish --> [*]
```

## What the state read returns

`autoscript_interface.get_microscope_state()` is the only place the agent learns
anything about the instrument. One reading carries:

| Key | Meaning |
| --- | --- |
| `voltage_kv` | acceleration voltage in kilovolts |
| `magnification` | nominal value of the current magnification step |
| `defocus_m` | objective defocus in metres, read from the STEM or TEM focusing branch according to the optical mode |
| `stage_position` | X, Y, Z in metres; A and B in degrees |
| `beam_blanked` | whether the beam blanker is engaged |
| `column_valve_open` | whether the column valve is open |
| `optical_mode` | `Stem` or `Tem`, which decides whether an acquisition is a scan or a camera frame |
| `detector` | the configured detector name, dwell time and image shape |

## Current state of the simulator

The demo drives either connector. Against `13.202.106.11:7521` the TCP connection
and the AutoScript handshake both succeed, and then every call is refused:

```
ApplicationServerException: AutoScript TEM product is not activated properly.
```

That is a server-side licence state on the simulator host, not something a client
can work around: the vendor reference manual states an offline licence is required
to use AutoScript in offline mode, and the client library exposes no licence API.
`POST /api/simcheck` reports the same three facts the page shows — reachable,
refused, and why. `--dry-run` needs none of it.

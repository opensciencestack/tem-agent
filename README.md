# TEM AI Agent

An autonomous, model-agnostic AI agent for Transmission Electron Microscopes (TEM). It automates complex microscopy workflows—such as autofocusing, fine sweeps, tomography tilt series, and specimen surveys—using either deterministic state-machine rules or large language models (**Claude** and **Gemini**).

The system includes both an interactive **Web Dashboard** (`demo_server.py`) and a scriptable **CLI** (`invoke_tem_ai_model.py`), backed by strict specimen safety controls and an interchangeable instrument abstraction layer.

---

## Table of Contents

- [Key Capabilities](#key-capabilities)
- [How It Works](#how-it-works)
  - [System Architecture](#system-architecture)
  - [Supported Workflows](#supported-workflows)
  - [Execution Modes: Dry-Run vs. Live](#execution-modes-dry-run-vs-live)
- [Repository Structure](#repository-structure)
- [Prerequisites](#prerequisites)
- [Getting Started](#getting-started)
  - [1. Clone and Create Virtual Environment](#1-clone-and-create-virtual-environment)
  - [2. Install Dependencies](#2-install-dependencies)
  - [3. Configure Environment Variables](#3-configure-environment-variables)
- [Starting the Application](#starting-the-application)
  - [Option A: Web Dashboard (Interactive UI)](#option-a-web-dashboard-interactive-ui)
  - [Option B: Command-Line Interface (CLI)](#option-b-command-line-interface-cli)
- [Connecting to the AutoScript Simulator](#connecting-to-the-autoscript-simulator)
  - [Network & Firewall Requirements](#network--firewall-requirements)
  - [Simulator Licensing](#simulator-licensing)
- [Safety Controls & Guardrails](#safety-controls--guardrails)
- [Extensibility](#extensibility)
  - [Adding a New Tool](#adding-a-new-tool)
  - [Connecting to a Physical Microscope](#connecting-to-a-physical-microscope)

---

## Key Capabilities

* **Autonomous Microscopy Loops**: Out-of-the-box routines for single-stage focus sweeps, two-stage coarse/fine sweeps, tomography series, and column surveys.
* **Deterministic or LLM-Driven Policies**:
  * `rule` (Default): Fast, deterministic state-machine policy. Requires **no LLM, no API keys, and zero cloud billing**.
  * `claude`: Anthropic Messages API backend for goal-driven autonomous reasoning.
  * `gemini`: Google Generative AI backend for tool-calling loops.
* **Live Web Dashboard**: Browser-based interface at `http://127.0.0.1:8765` featuring one-click simulator health checks, real-time stdout streaming, execution status, and interactive focus/variance charts.
* **Specimen Protection**: Software-enforced limits on stage travel, tilt angles, and lens defocus delta per action. Automatically blanks the electron beam and closes the column valve on completion or unhandled exceptions.
* **Hardware Abstraction**: Seamlessly switch between the Thermo Fisher AutoScript client, an in-memory mock simulator, or a custom physical instrument connector.

---

## How It Works

### System Architecture

The application cleanly decouples the decision engine (Policy), execution safeguards (Capabilities), and hardware communication (Connectors):

```mermaid
flowchart TD
  subgraph Frontend & Entry Points
    UI["Web UI (web/index.html)"] -->|"POST /api/run\nGET /api/state"| SRV["demo_server.py\n(HTTP Server)"]
    CLI["invoke_tem_ai_model.py\n(CLI Runner)"]
  end

  SRV --> AG["tem_agent.py (TEMAgent Loop)"]
  CLI --> AG

  subgraph Decision Layer
    AG <-->|"get_next_action()\npost_tool_results()"| POL["Policy Layer\n(rule | claude | gemini)"]
  end

  subgraph Safety & Execution Layer
    AG -->|"execute(tool_name, params)"| CAP["tem_capabilities.py\n(Safety Validation & Metrics)"]
    CAP -.->|"Schema Validation"| TOOLS["config/tools.yaml\n(13 Tool Schemas)"]
    CAP -.->|"Limits Enforcement"| LIM["config/instrument_autoscript.yaml\n(Safety Thresholds)"]
  end

  subgraph Hardware Abstraction
    CAP -->|"Live Mode"| CONN["connectors/autoscript_connector.py"]
    CAP -.->|"--dry-run Mode"| MOCK["tests/mock_connector.py\n(In-Memory Simulation)"]
    CONN --> IF["autoscript_interface.py\n(AutoScript Bindings)"]
    IF -->|"TCP Port 7521\nFramed Protocol"| SIM["AutoScript TEM Server / Simulator"]
  end
```

### Supported Workflows

1. **Focus Sweep (`focus`)**:
   Sweeps objective lens defocus across 7 points around the starting position, computes normalized image variance for each acquisition to assess sharpness, and restores the microscope to the peak focus defocus.
2. **Two-Stage Focus Sweep (`focus2`)**:
   Executes a coarse 7-point sweep to locate the focal zone, followed by a refined 5-point pass around the coarse peak before restoring the best focus.
3. **Tomography Tilt Series (`tomography`)**:
   Tilts the specimen stage through targeted angles (e.g. -20°, 0°, +20°), performs autofocus sweeps at each angle, captures projection images, and resets the stage to 0° resting state.
4. **Survey (`survey`)**:
   Reads microscope telemetry, verifies/opens the column valve, unblanks the beam, ensures STEM mode, configures the detector, and captures a reference image.

### Execution Modes: Dry-Run vs. Live

* **Dry-Run Mode (`--dry-run` or UI checkbox)**:
  Uses `MockTEMConnector` entirely in-memory. Does not require microscope hardware, AutoScript software, or network connectivity. Ideal for validating prompt policies, testing UI interactions, and CI/CD pipelines.
* **Live Mode**:
  Connects to a running Thermo Fisher AutoScript TEM server or simulator instance via TCP port `7521`. Requires the proprietary `autoscript_tem_microscope_client` Python package.

---

## Repository Structure

```
tem-agent/
├── demo_server.py             # HTTP server providing Web UI and REST API
├── invoke_tem_ai_model.py     # Canonical CLI entry point
├── tem_agent.py               # Core orchestrator loop (Action -> Execution -> Feedback)
├── tem_capabilities.py        # Tool execution engine with safety boundary enforcement
├── tool_registry.py           # Loads and validates tools from config/tools.yaml
├── instrument_registry.py     # Loads hardware safety constraints from config/
├── autoscript_interface.py    # Low-level bindings to Thermo Fisher AutoScript SDK
├── tem_context.txt            # System prompt and operational constraints for LLM backends
├── requirements.txt           # Python package dependencies
├── .env.example               # Template for environment configuration
│
├── web/
│   └── index.html             # Single-page web dashboard (UI, charts, Mermaid diagrams)
│
├── config/
│   ├── tools.yaml                 # Definitions and JSON schemas for all 13 tools
│   └── instrument_autoscript.yaml # Instrument safety limits (defocus, stage, voltage)
│
├── policies/
│   ├── base_policy.py         # Abstract base class for all policy implementations
│   ├── rule_policy.py         # Deterministic state-machine policy (model-free)
│   ├── claude_policy.py       # Anthropic Claude tool-calling policy
│   └── gemini_policy.py       # Google Gemini tool-calling policy
│
├── connectors/
│   └── autoscript_connector.py # Live microscope connector wrapping autoscript_interface
│
└── tests/
    └── mock_connector.py      # In-memory mock connector used in dry-run mode
```

---

## Prerequisites

* **Python 3.10+** (Python 3.10 to 3.12 recommended)
* **AutoScript Client SDK** (Required only for live connections to the microscope/simulator):
  Installed via Thermo Fisher wheel `autoscript_tem_microscope_client-*.whl`
* *(Optional)* **API Keys**:
  * `ANTHROPIC_API_KEY` for Claude (`claude-sonnet-4-6` or custom)
  * `GEMINI_API_KEY` for Gemini (`gemini-2.0-flash` or custom)

---

## Getting Started

### 1. Clone and Create Virtual Environment

Always run the agent inside a dedicated virtual environment so all AutoScript SDK packages and dependencies resolve correctly:

**On Windows (PowerShell):**
```powershell
cd d:\OSS\oss-devsetup\tem-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**On Linux / macOS (Bash):**
```bash
cd tem-agent
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

> [!NOTE]
> If connecting to a live simulator or real TEM instrument, ensure `autoscript_tem_microscope_client` is installed in this virtual environment. Verify with:
> ```bash
> python -c "import autoscript_tem_microscope_client; print('AutoScript SDK OK')"
> ```

### 3. Configure Environment Variables

Create your local `.env` file from the provided template:

```bash
cp .env.example .env
```

Open `.env` and set your configuration:

```env
# AutoScript TEM server (simulator or physical instrument)
AUTOSCRIPT_HOST=65.1.231.167
AUTOSCRIPT_PORT=7521

# Only needed if running --model claude
ANTHROPIC_API_KEY=your_anthropic_api_key_here
CLAUDE_MODEL=claude-sonnet-4-6

# Only needed if running --model gemini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-2.0-flash
```

---

## Starting the Application

You can operate the TEM Agent either through the **interactive Web Dashboard** or the **scriptable CLI**.

### Option A: Web Dashboard (Interactive UI)

The Web Dashboard allows you to test network connectivity, verify simulator licensing, select workflows, trigger runs, and inspect live feedback charts without memorizing CLI arguments.

1. **Start the demo server** using the virtual environment Python:

   **Windows:**
   ```powershell
   .\.venv\Scripts\python.exe demo_server.py
   ```

   **Linux / macOS:**
   ```bash
   python demo_server.py
   ```

   By default, the server listens on `http://127.0.0.1:8765`. To bind to a custom port or host:
   ```bash
   python demo_server.py --port 9000 --host 0.0.0.0
   ```

2. **Open the Dashboard**:
   Navigate to [http://127.0.0.1:8765](http://127.0.0.1:8765) in your web browser.

3. **Using the Dashboard**:
   * **Check Simulator**: Click the **Check simulator** button. It connects to the configured `host:port` and reports whether the server is reachable and properly licensed (`answered, optical mode 'Tem'`).
   * **Choose Policy**: Select `rule` (no API key required), `claude`, or `gemini`.
   * **Toggle Dry-Run**: Keep **Dry-run (mock)** checked to simulate locally without hardware, or uncheck it to execute commands against the live simulator.
   * **Run Loop**: Choose between *Focus sweep*, *Focus sweep (two-stage)*, *Tomography*, or *Survey*, and click **Run loop**.
   * **Monitor Progress**: Watch stdout stream line-by-line, inspect real-time focus variance graphs, and review the best observed defocus upon completion.

---

### Option B: Command-Line Interface (CLI)

`invoke_tem_ai_model.py` is the canonical entry point for automated or scriptable runs.

#### 1. Dry-Run Mode (No hardware or API keys required with rule policy)

```bash
# Deterministic rule policy in dry-run mode
python invoke_tem_ai_model.py --model rule --dry-run

# Run a two-stage focus sweep in dry-run
python invoke_tem_ai_model.py --model rule --goal focus2 --dry-run

# Run dry-run with an LLM backend (requires API key)
python invoke_tem_ai_model.py --model claude --dry-run
python invoke_tem_ai_model.py --model gemini --dry-run
```

#### 2. Live Simulator Mode (Connected to AutoScript)

Ensure `AUTOSCRIPT_HOST` and `AUTOSCRIPT_PORT` are configured in `.env`.

```bash
# Run standard focus sweep against the simulator using rule policy
python invoke_tem_ai_model.py --model rule

# Run tomography tilt series against the simulator
python invoke_tem_ai_model.py --model rule --goal tomography

# Run with Claude on custom natural-language instructions
python invoke_tem_ai_model.py --model claude \
  --goal "Perform a focus sweep at 4300x magnification and restore the peak focus."

# Cap the maximum iterations for safety
python invoke_tem_ai_model.py --model rule --max-iterations 30
```

---

## Connecting to the AutoScript Simulator

When running against a remote or hosted AutoScript simulator (e.g. AWS EC2 Windows instance):

### Network & Firewall Requirements

1. **Microscope Server Port**: AutoScript communicates over TCP port `7521`.
2. **AWS Security Group**:
   Ensure the EC2 Security Group contains an inbound rule:
   * **Type**: Custom TCP
   * **Port**: `7521`
   * **Source**: Your client IP address or CIDR range.
3. **Windows Firewall on the Simulator Machine**:
   If the simulator runs on Windows Server, ensure port 7521 is allowed in Windows Defender Firewall:
   ```powershell
   netsh advfirewall firewall add rule name="AutoScript Simulator 7521" dir=in action=allow protocol=TCP localport=7521
   ```

### Simulator Licensing

The Thermo Fisher AutoScript TEM server requires an offline product license to execute hardware calls.
* If a host accepts TCP connections but lacks a valid license, it returns:
  `ApplicationServerException: AutoScript TEM product is not activated properly.`
* The Web UI's `/api/simcheck` endpoint and the **Check simulator** button automatically probe for this condition and flag it before starting a run.

---

## Safety Controls & Guardrails

All tool calls pass through `tem_capabilities.py` and are validated against limits defined in `config/instrument_autoscript.yaml`:

| Parameter | Safety Limit | Behavior on Violation |
|---|---|---|
| **Defocus Step** | ±5 µm per action | Action rejected with error message |
| **Stage Translation** | ±10 µm (X, Y, Z) per action | Action rejected |
| **Stage Tilt** | ±45° (Alpha, Beta) per action | Action rejected |
| **Acceleration Voltage** | 60 – 300 kV | Value clamped / rejected |
| **Focus Restoration** | Within ±10 nm of peak | Required by `finish` tool |
| **Exit Specimen Protection** | Always enforced | Automatically blanks beam and closes column valve on loop exit or crash |

---

## Extensibility

### Adding a New Tool

1. **Define Schema**: Add the tool signature and parameters to [`config/tools.yaml`](file:///d:/OSS/oss-devsetup/tem-agent/config/tools.yaml).
2. **Implement Capability**: Add the validation logic and handler method in [`tem_capabilities.py`](file:///d:/OSS/oss-devsetup/tem-agent/tem_capabilities.py).
3. **Add Hardware Binding**: Add the underlying call to [`connectors/autoscript_connector.py`](file:///d:/OSS/oss-devsetup/tem-agent/connectors/autoscript_connector.py) and [`tests/mock_connector.py`](file:///d:/OSS/oss-devsetup/tem-agent/tests/mock_connector.py).

*No changes are required in `tem_agent.py`, `invoke_tem_ai_model.py`, or any policy file.*

### Connecting to a Physical Microscope

To target a different TEM hardware platform (e.g. JEOL, Nion, or direct Python-TEM bindings):

1. Implement a new connector in `connectors/my_tem_connector.py` adhering to the interface in `connectors/autoscript_connector.py`.
2. Define instrument safety thresholds in `config/instrument_custom.yaml`.
3. Pass the custom configuration at runtime:
   ```bash
   python invoke_tem_ai_model.py --instrument-config config/instrument_custom.yaml
   ```

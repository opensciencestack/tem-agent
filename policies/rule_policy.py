#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RuleTEMPolicy: a deterministic, model-free policy for the TEM Agent.

It implements the same BaseTEMPolicy interface as the Claude and Gemini
policies, so it drives the identical agent loop, tool layer and safety limits --
but every decision is a rule, and no AI backend, API key or network call is
involved. Its purpose is demonstrating the agent's optimisation loops against
the AutoScript simulator.

The goal text selects the loop by keyword:

    focus        one coarse defocus sweep, then a quadratic peak pick
    focus2       the same sweep twice -- coarse, then a fine pass around the peak
    survey       read the state, set up the detector, take one image
    tomography   a tilt series, refocusing at every angle

Examples:
    python invoke_tem_ai_model.py --model rule --goal focus2
    python invoke_tem_ai_model.py --model rule --goal tomography
"""

import math
from policies.base_policy import BaseTEMPolicy


class RuleTEMPolicy(BaseTEMPolicy):
    """Deterministic policy: a small state machine per optimisation loop."""

    def __init__(self, dwell_time_s: float = 2e-6, image_shape=(512, 512),
                 detector: str = "HAADF", optical_mode="Stem",
                 stages=None, angles=None, magnification: int = None,
                 voltage_kv: float = None, tomo_half_range_m: float = 150e-9,
                 tomo_steps: int = 3):
        """
        Args:
            dwell_time_s:  STEM dwell (or camera exposure) per acquisition.
            image_shape:   [width, height] for each acquisition.
            detector:      scanning detector name for STEM acquisitions.
            optical_mode:  'Stem' or 'Tem' -- set during setup when the tool exists.
            stages:        focus sweeps as (half_range_m, steps), coarse to fine.
            angles:        tilt angles in degrees for the tomography loop.
            magnification: optional magnification preset to select during setup.
            voltage_kv:    optional acceleration voltage to set during setup.
        """
        self.dwell_time_s = dwell_time_s
        self.image_shape = list(image_shape)
        self.detector = detector
        self.optical_mode = optical_mode
        self.stages = list(stages) if stages else [(300e-9, 7), (100e-9, 5)]
        self.angles = list(angles) if angles else [-20.0, 0.0, 20.0]
        self.magnification = magnification
        self.voltage_kv = voltage_kv
        self.tomo_half_range_m = tomo_half_range_m
        self.tomo_steps = max(3, int(tomo_steps))

    # ---------------------------------------------------------------- interface

    def reset(self, goal_text: str, tools: list):
        self.tools = {t["name"] for t in tools}
        self.goal = (goal_text or "").strip()
        self.mode = self._parse_goal(self.goal)

        self.phase = "start"
        self.pending = []
        self._seq = 0

        self.state = {}                 # last get_microscope_state reading
        self.observations = []          # (defocus_m, normalized_variance)
        self.stage_index = 0
        self.sweep_plan = []
        self.sweep_step = 0.0
        self.sweep_pos = 0
        self.best = None                # (defocus_m, score)
        self.angle_index = 0
        self.tilt_now = 0.0
        self.setup_left = []
        self.corrective_set = False
        self.tomo_best = None

        print(f"[RulePolicy] Loop '{self.mode}' selected from goal: {self.goal!r}")

    def get_next_action(self):
        if self.pending:
            return [self.pending.pop(0)], "Executing queued setup call."
        return self._dispatch()

    def post_tool_results(self, results: list):
        if not results:
            return
        self.last = results[-1].get("content_dict") or {}
        self.last_error = results[-1].get("is_error", False)

    # -------------------------------------------------------------- goal parsing

    def _parse_goal(self, goal: str) -> str:
        g = goal.lower()
        if "tomograph" in g or "tilt series" in g or "tilt-series" in g:
            return "tomography"
        if "survey" in g or "acquire one" in g:
            return "survey"
        if "focus2" in g or "two-stage" in g or "coarse" in g:
            return "focus2"
        if "focus" in g or not g:
            return "focus"
        return "focus"

    # ------------------------------------------------------------------- helpers

    def _call(self, name: str, **params):
        self._seq += 1
        return {"id": f"rule-{self._seq}", "name": name, "input": params}

    def _read_state(self):
        return self._call("get_microscope_state")

    def _setup_calls(self):
        """Valve, beam, detector and optics setup, in the order the tools require."""
        calls = [self._call("open_column_valve"), self._call("unblank_beam")]
        if "set_optical_mode" in self.tools:
            calls.append(self._call("set_optical_mode", mode=self.optical_mode))
        if self.voltage_kv is not None:
            calls.append(self._call("set_acceleration_voltage", voltage_kv=self.voltage_kv))
        if self.magnification is not None:
            calls.append(self._call("set_magnification", magnification=self.magnification))
        calls.append(self._call("configure_detector", detector_type=self.detector,
                                dwell_time_s=self.dwell_time_s, image_shape=self.image_shape))
        return calls

    # ------------------------------------------------------------------ dispatch

    def _dispatch(self):
        handler = getattr(self, f"_phase_{self.phase}", None)
        if handler is None:
            return [], f"No handler for phase '{self.phase}'."
        return handler()

    # ---- shared: read state -------------------------------------------------

    def _phase_start(self):
        self.phase = "after_start"
        return [self._read_state()], "Reading the microscope state."

    def _phase_after_start(self):
        if self.last_error:
            reason = self.last.get("error", "the microscope did not answer")
            return [], (f"Stopping before touching anything: the microscope refused the "
                        f"state read. {reason}")
        self.state = self.last
        current_df = float(self.state.get("current_defocus_m", 0.0))
        self.start_df = current_df
        self.tilt_now = float(self.state.get("stage_position", {}).get("A", 0.0))

        if self.mode == "survey":
            self.pending = self._setup_calls()
            self.phase = "survey_acquire"
            return [self.pending.pop(0)], "Setting up the microscope."
        if self.mode == "tomography":
            self.pending = self._setup_calls()
            self.phase = "tomo_tilt"
            return [self.pending.pop(0)], "Setting up the microscope."

        # focus / focus2 -- begin the first sweep
        print(f"[RulePolicy] Starting defocus: {current_df*1e9:.1f} nm")
        self.observations = []
        self.stage_index = 0
        self._plan_sweep(center=current_df)
        self.phase = "sweep_set"
        return self._dispatch()

    # ---- focus sweeps -------------------------------------------------------

    def _plan_sweep(self, center: float):
        half_range, steps = self.stages[self.stage_index]
        steps = max(3, int(steps))
        self.sweep_step = (2.0 * half_range) / (steps - 1)
        self.sweep_plan = [center - half_range + i * self.sweep_step for i in range(steps)]
        self.sweep_pos = 0
        print(f"[RulePolicy] Sweep {self.stage_index + 1}/{len(self.stages)}: "
              f"{self.sweep_plan[0]*1e9:+.0f} nm .. {self.sweep_plan[-1]*1e9:+.0f} nm "
              f"in {steps} points ({self.sweep_step*1e9:.0f} nm apart)")

    def _phase_sweep_set(self):
        if self.sweep_pos >= len(self.sweep_plan):
            self.phase = "sweep_pick"
            return self._dispatch()
        target = self.sweep_plan[self.sweep_pos]
        self.phase = "sweep_acquire"
        return [self._call("set_defocus", target_df=target)], \
               f"Sweep {self.stage_index + 1}, point {self.sweep_pos + 1}/{len(self.sweep_plan)}: set defocus."

    def _phase_sweep_acquire(self):
        self.phase = "sweep_record"
        return [self._call("acquire_image")], "Acquiring an image to score this defocus."

    def _phase_sweep_record(self):
        score = float(self.last.get("normalized_variance", 0.0))
        df = float(self.last.get("current_defocus_m", self.sweep_plan[self.sweep_pos]))
        self.observations.append((df, score))
        print(f"[RulePolicy]   {df*1e9:+8.1f} nm -> normalized variance {score:.6f}")
        self.sweep_pos += 1
        self.phase = "sweep_set"
        return self._dispatch()

    def _phase_sweep_pick(self):
        best_df, best_score = max(self.observations, key=lambda o: o[1])
        if self.best is None or best_score > self.best[1]:
            self.best = (best_df, best_score)

        more_stages = self.mode == "focus2" and self.stage_index < len(self.stages) - 1
        if more_stages:
            self.stage_index += 1
            print(f"[RulePolicy] Coarse peak at {best_df*1e9:+.1f} nm "
                  f"(score {best_score:.6f}); refining around it.")
            self._plan_sweep(center=best_df)
            self.phase = "sweep_set"
            return self._dispatch()

        print(f"[RulePolicy] Best defocus {self.best[0]*1e9:+.1f} nm "
              f"with normalized variance {self.best[1]:.6f}; restoring it.")
        self.phase = "tune_check"
        return [self._call("set_defocus", target_df=self.best[0])], "Restore the best defocus."

    def _phase_tune_check(self):
        actual = float(self.last.get("actual_defocus_m", self.best[0]))
        tolerance = 10e-9
        if not self.corrective_set and abs(actual - self.best[0]) > tolerance:
            self.corrective_set = True
            corrected = self.best[0] + (self.best[0] - actual)
            print(f"[RulePolicy] Landed {abs(actual-self.best[0])*1e9:.0f} nm off; "
                  f"correcting by commanding {corrected*1e9:+.1f} nm.")
            return [self._call("set_defocus", target_df=corrected)], "Correct the resting defocus."
        self.phase = "finish"
        return self._dispatch()

    # ---- survey -------------------------------------------------------------

    def _phase_survey_acquire(self):
        self.phase = "survey_done"
        return [self._call("acquire_image")], "Acquiring one image."

    def _phase_survey_done(self):
        self.phase = "finish"
        return self._dispatch()

    # ---- tomography ---------------------------------------------------------

    def _phase_tomo_tilt(self):
        if self.angle_index >= len(self.angles):
            if abs(self.tilt_now) > 0.01:
                delta = -self.tilt_now
                self.phase = "tomo_restore_read"
                return [self._call("move_stage", dA=delta)], \
                       f"Restoring the stage to 0 degrees (delta {delta:+.1f})."
            self.phase = "finish"
            return self._dispatch()

        target = self.angles[self.angle_index]
        delta = target - self.tilt_now
        self.phase = "tomo_tilt_read"
        return [self._call("move_stage", dA=delta)], \
               f"Angle {target:+.0f}: tilt by {delta:+.1f} degrees."

    def _phase_tomo_tilt_read(self):
        self.tilt_now = float(self.last.get("current_stage_position", {}).get("A", self.tilt_now))
        # A short refocus sweep at this angle; the coarse peak came from the first angle.
        self.observations = []
        half_range, steps = self.tomo_half_range_m, self.tomo_steps
        center = float(self.state.get("current_defocus_m", self.start_df))
        self.sweep_step = (2.0 * half_range) / (steps - 1)
        self.sweep_plan = [center - half_range + i * self.sweep_step for i in range(steps)]
        self.sweep_pos = 0
        self.phase = "tomo_sweep_set"
        return self._dispatch()

    def _phase_tomo_sweep_set(self):
        if self.sweep_pos >= len(self.sweep_plan):
            best_df, _ = max(self.observations, key=lambda o: o[1])
            self.phase = "tomo_restore"
            return [self._call("set_defocus", target_df=best_df)], \
                   f"Angle {self.angles[self.angle_index]:+.0f}: restore best focus."
        target = self.sweep_plan[self.sweep_pos]
        self.phase = "tomo_sweep_acquire"
        return [self._call("set_defocus", target_df=target)], \
               f"Angle {self.angles[self.angle_index]:+.0f}: refocus point {self.sweep_pos + 1}."

    def _phase_tomo_sweep_acquire(self):
        self.phase = "tomo_sweep_record"
        return [self._call("acquire_image")], "Score this focus."

    def _phase_tomo_sweep_record(self):
        score = float(self.last.get("normalized_variance", 0.0))
        df = float(self.last.get("current_defocus_m", 0.0))
        self.observations.append((df, score))
        if self.tomo_best is None or score > self.tomo_best[1]:
            self.tomo_best = (df, score)
        print(f"[RulePolicy]   tilt {self.tilt_now:+.0f} deg, {df*1e9:+8.1f} nm -> {score:.6f}")
        self.sweep_pos += 1
        self.phase = "tomo_sweep_set"
        return self._dispatch()

    def _phase_tomo_restore(self):
        self.phase = "tomo_projection"
        return self._dispatch()

    def _phase_tomo_projection(self):
        angle = self.angles[self.angle_index]
        print(f"[RulePolicy] Angle {angle:+.0f}: focused; acquiring the projection image.")
        self.angle_index += 1
        self.phase = "tomo_tilt"
        return [self._call("acquire_image")], f"Acquire the projection at {angle:+.0f} degrees."

    def _phase_tomo_restore_read(self):
        # Tilt is back at zero; resting at the best focus observed across the series
        # is what the finish check validates, so set it explicitly before finishing.
        self.tilt_now = float(self.last.get("current_stage_position", {}).get("A", 0.0))
        if self.tomo_best is not None:
            self.phase = "tomo_final_restore"
            return [self._call("set_defocus", target_df=self.tomo_best[0])], \
                   "Restore the best focus observed across the tilt series."
        self.phase = "finish"
        return self._dispatch()

    def _phase_tomo_final_restore(self):
        self.phase = "finish"
        return self._dispatch()

    # ---- finish -------------------------------------------------------------

    def _phase_finish(self):
        if self.mode == "tomography":
            summary = (f"Tomography tilt series complete at angles "
                       f"{', '.join(f'{a:+.0f}' for a in self.angles)} degrees, "
                       f"refocused at every angle.")
        elif self.mode == "survey":
            summary = "Microscope state read and one image acquired."
        else:
            stages = "coarse then fine" if self.mode == "focus2" else "single"
            summary = (f"Focus optimised by a {stages} defocus sweep; "
                       f"best {self.best[0]*1e9:+.1f} nm at normalized variance {self.best[1]:.6f}.")
        self.phase = "done"
        return [self._call("finish", summary=summary)], summary

    def _phase_done(self):
        return [], "Task complete."

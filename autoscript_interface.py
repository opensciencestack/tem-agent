#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoScript Interface module.
Provides an adapter layer between the TEM Agent and the Thermo Scientific AutoScript TEM client.
Exposes the same top-level functional API as microscope_interface.py.
"""

import os
import sys
import math
import time
from typing import Dict, Any, Tuple

# Delay import of structures until connect is established (or import them normally)
try:
    from autoscript_tem_microscope_client import TemMicroscopeClient
    from autoscript_tem_microscope_client.structures import StagePosition, AdornedImage
    from autoscript_tem_microscope_client.enumerations import ColumnValvesState
except ImportError:
    # If the user is running in a different environment, they will get an import error on connect
    pass

# Global client connection instance
client = None

# Global detector and acquisition configurations
detector_config = {
    'type': 'HAADF',      # Default detector
    'dwell_time': 2e-6,   # Default STEM dwell time in seconds / default Camera exposure time
    'image_shape': (256, 256)
}

class AutoScriptImageAdapter:
    """
    Adapter wrapper class for the AutoScript AdornedImage.
    Provides standard attributes expected by the TEM Agent workflows.
    """
    def __init__(self, raw_image, quality: float, offset_x: float = 0.0, offset_y: float = 0.0):
        self.raw_image = raw_image
        self.pixel_data = raw_image.data if raw_image else None
        self.metadata = raw_image.metadata if raw_image else None
        self.quality = quality
        
        # TODO: Real lateral offset/shift estimation should be implemented later 
        # using image registration or cross-correlation. For now, offsets are set to 0.0
        self.offset_x = offset_x
        self.offset_y = offset_y

    def __repr__(self):
        return f"AutoScriptImageAdapter(quality={self.quality:.4f}, offset=({self.offset_x*1e9:.1f}nm, {self.offset_y*1e9:.1f}nm))"

def _check_connection():
    global client
    if client is None:
        raise RuntimeError("AutoScript client is not connected! Call connect() first.")

def connect():
    """
    Establishes connection to the AutoScript server using environment variables.
    """
    global client
    if client is None:
        try:
            from autoscript_tem_microscope_client import TemMicroscopeClient
            client = TemMicroscopeClient()
        except ImportError as e:
            print(f"[AutoScript] Failed to import TemMicroscopeClient: {e}")
            raise

    host = os.environ.get("AUTOSCRIPT_HOST")
    port_str = os.getenv("AUTOSCRIPT_PORT", "7521")
    
    if not host:
        raise ValueError("AUTOSCRIPT_HOST environment variable is not set! Set it in tem-agent/.env (see .env.example).")
        
    try:
        port = int(port_str)
    except ValueError:
        raise ValueError(f"AUTOSCRIPT_PORT '{port_str}' is not a valid integer.")
        
    print(f"[AutoScript] Connecting to TEM server at network endpoint: {host}:{port}...")
    client.connect(host, port)
    print("[AutoScript] Connection established.")

def disconnect():
    """
    Closes the connection to the AutoScript server.
    """
    global client
    if client is not None:
        print("[AutoScript] Disconnecting from server...")
        try:
            client.disconnect()
            print("[AutoScript] Disconnected.")
        except Exception as e:
            print(f"[AutoScript] Error during disconnect: {e}")
        finally:
            client = None

def get_microscope_state() -> dict:
    """
    Retrieves the current state of the microscope and maps it to a standard dictionary.
    Raises NotImplementedError if any critical capability is unavailable.
    """
    _check_connection()
    
    # 1. Read stage position
    try:
        pos = client.specimen.stage.position
        stage_pos = {
            'X': float(pos.x),
            'Y': float(pos.y),
            'Z': float(pos.z),
            'A': float(math.degrees(pos.a)),
            'B': float(math.degrees(pos.b))
        }
    except Exception as e:
        raise NotImplementedError(f"Stage position read failed/unsupported: {e}")

    # 2. Read active optical mode and defocus
    try:
        opt_mode = client.optics.optical_mode
        if opt_mode == 'Stem':
            defocus = client.optics.focusing.stem.objective.defocus
        else:
            defocus = client.optics.focusing.tem.defocus
    except Exception as e:
        # Fallback to deprecated optics.defocus if focusing structures are unavailable
        try:
            defocus = client.optics.defocus
        except Exception as e2:
            raise NotImplementedError(f"Defocus read failed/unsupported: {e} | Fallback failed: {e2}")

    # 3. Read magnification
    try:
        mag = client.optics.magnification.value.nominal
    except Exception as e:
        raise NotImplementedError(f"Magnification read failed/unsupported: {e}")

    # 4. Read acceleration voltage
    try:
        voltage_v = client.optics.acceleration_voltage.value
        voltage_kv = voltage_v / 1000.0
    except Exception as e:
        raise NotImplementedError(f"Acceleration voltage read failed/unsupported: {e}")

    # 5. Read column valve state
    try:
        valve_state = client.vacuum.column_valves.state
        column_valve_open = (valve_state == 'Opened' or str(valve_state).lower() == 'opened')
    except Exception as e:
        raise NotImplementedError(f"Column valve state read failed/unsupported: {e}")

    # 6. Read beam blanked state
    try:
        blanked = client.optics.blanker.is_beam_blanked
    except Exception:
        try:
            blanked = client.optics.is_beam_blanked
        except Exception as e:
            raise NotImplementedError(f"Beam blanked state read failed/unsupported: {e}")

    # Expose detector type and settings compatible with existing workflows
    return {
        'voltage_kv': voltage_kv,
        'magnification': mag,
        'defocus_m': defocus,
        'stage_position': stage_pos,
        'beam_blanked': blanked,
        'column_valve_open': column_valve_open,
        'optical_mode': opt_mode,
        'detector': {
            'type': detector_config.get('type', 'HAADF'),
            'dwell_time_s': detector_config.get('dwell_time', 2e-6),
            'image_shape': detector_config.get('image_shape', (256, 256))
        }
    }

def set_acceleration_voltage(voltage_kv: float):
    """
    Sets the acceleration voltage in kV.
    """
    _check_connection()
    try:
        client.optics.acceleration_voltage.value = float(voltage_kv * 1000.0)
    except Exception as e:
        raise NotImplementedError(f"set_acceleration_voltage failed/unsupported: {e}")

def set_optical_mode(mode: str):
    """
    Sets the column optical mode. The value is the string behind the OpticalMode
    enumeration: 'Stem' or 'Tem'. An acquisition reads this mode back to decide
    whether it takes a STEM scan or a camera image, so it must be set before one.
    """
    _check_connection()
    from autoscript_tem_microscope_client.enumerations import OpticalMode
    valid = (OpticalMode.STEM, OpticalMode.TEM)
    if mode not in valid:
        raise ValueError(f"Optical mode must be one of {list(valid)}, got {mode!r}.")
    try:
        client.optics.optical_mode = mode
    except Exception as e:
        raise NotImplementedError(f"set_optical_mode failed/unsupported: {e}")

def set_magnification(mag: int):
    """
    Sets the magnification value.
    """
    _check_connection()
    try:
        available = client.optics.magnification.available_values
        if not available:
            raise RuntimeError("No available magnification values returned by the microscope.")
        best_match = min(available, key=lambda x: abs(x.nominal - mag))
        print(f"[AutoScript Interface] Selected closest magnification: {best_match.nominal}x (label: {best_match.label}) for target {mag}x")
        client.optics.magnification.value = best_match
    except Exception as e:
        raise NotImplementedError(f"set_magnification failed/unsupported: {e}")

def set_defocus(target_df: float):
    """
    Sets the objective lens defocus.
    """
    _check_connection()
    try:
        opt_mode = client.optics.optical_mode
        if opt_mode == 'Stem':
            client.optics.focusing.stem.objective.defocus = target_df
        else:
            client.optics.focusing.tem.defocus = target_df
    except Exception:
        try:
            client.optics.defocus = target_df
        except Exception as e:
            raise NotImplementedError(f"set_defocus failed/unsupported: {e}")

def move_stage(dX: float = 0, dY: float = 0, dZ: float = 0, dA: float = 0, dB: float = 0):
    """
    Moves the stage relative to the current position.
    """
    _check_connection()
    try:
        from autoscript_tem_microscope_client.structures import StagePosition
        pos = StagePosition(x=dX, y=dY, z=dZ, a=math.radians(dA), b=math.radians(dB))
        client.specimen.stage.relative_move(pos)
    except Exception as e:
        raise NotImplementedError(f"move_stage (relative_move) failed/unsupported: {e}")

def configure_detector(detector_type: str, settings: dict):
    """
    Saves the configured detector settings locally for the next acquisition.
    """
    global detector_config
    detector_config['type'] = detector_type
    detector_config.update(settings)
    print(f"[AutoScript Interface] Saved detector configuration: {detector_type} with settings: {settings}")

def blank_beam():
    """
    Blanks the electron beam.
    """
    _check_connection()
    try:
        client.optics.blanker.blank()
    except Exception:
        try:
            client.optics.blank()
        except Exception as e:
            raise NotImplementedError(f"blank_beam failed/unsupported: {e}")

def unblank_beam():
    """
    Unblanks the electron beam.
    """
    _check_connection()
    try:
        client.optics.blanker.unblank()
    except Exception:
        try:
            client.optics.unblank()
        except Exception as e:
            raise NotImplementedError(f"unblank_beam failed/unsupported: {e}")

def open_column_valve():
    """
    Opens the column valve.
    """
    _check_connection()
    try:
        client.vacuum.column_valves.open()
    except Exception as e:
        raise NotImplementedError(f"open_column_valve failed/unsupported: {e}")

def close_column_valve():
    """
    Closes the column valve.
    """
    _check_connection()
    try:
        client.vacuum.column_valves.close()
    except Exception as e:
        raise NotImplementedError(f"close_column_valve failed/unsupported: {e}")

def acquire_image(dwell: float = None, shape: tuple = None) -> Tuple[AutoScriptImageAdapter, float]:
    """
    Acquires an image from the microscope based on active optical mode and configuration.
    
    Returns:
        image (AutoScriptImageAdapter): Wrapped AdornedImage.
        pixel_size (float): Real-world size of one pixel in meters.
    """
    _check_connection()
    global detector_config
    
    try:
        opt_mode = client.optics.optical_mode
    except Exception as e:
        raise NotImplementedError(f"Optical mode read failed: {e}")
        
    # Map requested pixel size/dimension to nearest supported ImageSize
    size = shape[0] if shape else detector_config.get('image_shape', (256, 256))[0]
    if opt_mode != 'Stem' and size < 512:
        size = 512
        
    allowed_sizes = [128, 256, 512, 1024, 2048, 4096]
    if size not in allowed_sizes:
        size = min(allowed_sizes, key=lambda x: abs(x - size))

    if opt_mode == 'Stem':
        # STEM scan acquisition
        dwell_time = dwell if dwell is not None else detector_config.get('dwell_time', 2e-6)
        detector_name = detector_config.get('type', 'HAADF')
        if detector_name not in ['HAADF', 'DF-S', 'DF4', 'DF2', 'BF-S', 'BF']:
            detector_name = 'HAADF'
            
        print(f"[AutoScript] Acquiring STEM image using {detector_name} detector (Size: {size}, Dwell: {dwell_time*1e6:.1f}us)...")
        try:
            raw_img = client.acquisition.acquire_stem_image(
                scanning_detector=detector_name,
                size=int(size),
                dwell_time=float(dwell_time)
            )
        except Exception as e:
            raise NotImplementedError(f"acquire_stem_image failed: {e}")
    else:
        # TEM CCD/Camera acquisition
        exposure_time = dwell if dwell is not None else detector_config.get('dwell_time', 0.1)
        detector_name = detector_config.get('type', 'BM-Ceta')
        if detector_name not in ['BM-Ceta', 'BM-Falcon', 'Flucam', 'BM-Empad']:
            detector_name = 'BM-Ceta'
            
        print(f"[AutoScript] Acquiring camera image using {detector_name} detector (Size: {size}, Exposure: {exposure_time:.2f}s)...")
        try:
            raw_img = client.acquisition.acquire_camera_image(
                camera_detector=detector_name,
                size=int(size),
                exposure_time=float(exposure_time)
            )
        except Exception as e:
            raise NotImplementedError(f"acquire_camera_image failed: {e}")

    # Extract pixel size from image metadata
    pixel_size = 1e-9  # Default fallback
    if raw_img and hasattr(raw_img, 'metadata') and raw_img.metadata:
        try:
            ps = raw_img.metadata.binary_result.pixel_size
            if hasattr(ps, 'x'):
                pixel_size = float(ps.x)
            elif isinstance(ps, (list, tuple)) and len(ps) > 0:
                pixel_size = float(ps[0])
            else:
                pixel_size = float(ps)
        except Exception:
            pass

    # Evaluate image quality
    quality = evaluate_image_quality(raw_img)
    
    # Wrap in our adapter (offsets are set to 0.0 with a TODO in the class definition)
    adapted_img = AutoScriptImageAdapter(raw_image=raw_img, quality=quality, offset_x=0.0, offset_y=0.0)
    return adapted_img, pixel_size

def evaluate_image_quality(image, metric: str = 'normvar') -> float:
    """
    Computes a real, pixel-based quality score for an image.
    Uses Normalized Variance = Variance(pixel_data) / Mean(pixel_data)^2.
    """
    if isinstance(image, AutoScriptImageAdapter):
        pixel_data = image.pixel_data
    elif hasattr(image, 'data'):
        pixel_data = image.data
    else:
        pixel_data = image

    if pixel_data is None:
        return 0.0

    try:
        import numpy as np
        # Convert flat list or numpy array values
        arr = np.array(pixel_data, dtype=float)
        mean = np.mean(arr)
        if abs(mean) < 1e-9:
            return 0.0
        var = np.var(arr)
        # Normalized variance is a proxy for sharpness/contrast
        score = float(var / (mean ** 2))
        return score
    except Exception as e:
        raise NotImplementedError(f"Image quality evaluation failed: {e}")

def run_autofocus(range_df: float = 500e-9) -> float:
    """
    Triggers the actual AutoScript autofocus auto-function depending on mode.
    Returns the found optimal defocus value.
    """
    _check_connection()
    global detector_config
    
    try:
        opt_mode = client.optics.optical_mode
    except Exception as e:
        raise NotImplementedError(f"Optical mode read failed: {e}")
        
    size = detector_config.get('image_shape', (256, 256))[0]
    if opt_mode != 'Stem' and size < 512:
        size = 512
    allowed_sizes = [128, 256, 512, 1024, 2048, 4096]
    if size not in allowed_sizes:
        size = min(allowed_sizes, key=lambda x: abs(x - size))

    if opt_mode == 'Stem':
        from autoscript_tem_microscope_client.structures import RunStemAutoFocusSettings
        detector_name = detector_config.get('type', 'HAADF')
        if detector_name not in ['HAADF', 'DF-S', 'DF4', 'DF2', 'BF-S', 'BF']:
            detector_name = 'HAADF'
        dwell_time = detector_config.get('dwell_time', 2e-6)
        print(f"[AutoScript] Running STEM Auto Focus using detector '{detector_name}'...")
        try:
            settings = RunStemAutoFocusSettings(
                scanning_detector=detector_name,
                size=int(size),
                dwell_time=float(dwell_time)
            )
            res = client.auto_functions.run_stem_auto_focus(settings)
            print(f"[AutoScript] STEM Auto Focus finished. Defocus set to: {res.defocus*1e9:.1f} nm")
            return float(res.defocus)
        except Exception as e:
            raise NotImplementedError(f"run_stem_auto_focus failed: {e}")
    else:
        from autoscript_tem_microscope_client.structures import RunBeamTiltAutoFocusSettings
        detector_name = detector_config.get('type', 'BM-Ceta')
        if detector_name not in ['BM-Ceta', 'BM-Falcon', 'Flucam', 'BM-Empad']:
            detector_name = 'BM-Ceta'
        exposure_time = detector_config.get('dwell_time', 0.1)
        print(f"[AutoScript] Running TEM Beam Tilt Auto Focus using detector '{detector_name}'...")
        try:
            settings = RunBeamTiltAutoFocusSettings(
                camera_detector=detector_name,
                size=int(size),
                exposure_time=float(exposure_time)
            )
            res = client.auto_functions.run_beam_tilt_auto_focus(settings)
            print(f"[AutoScript] TEM Auto Focus finished. Defocus set to: {res.defocus*1e9:.1f} nm")
            return float(res.defocus)
        except Exception as e:
            raise NotImplementedError(f"run_beam_tilt_auto_focus failed: {e}")

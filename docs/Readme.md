BatRack
===

Sense and record bats based on visuals, audio and VHF signals.

<figure class="video_container">
  <video controls="true" allowfullscreen="true">
    <source src="schwaermende_mbecs.mp4" type="video/mp4">
  </video>
</figure>

## Configuring BatRack

BatRack is configured through a configuration file, which is loaded on startup.

### Continous Operation Mode

In the basic *continuous operation mode*, BatRack is configured through the options available in the `[BatRack]` section.

```ini
[BatRack]
logging_level = INFO
data_path = /data/
duty_cycle_s = 10
always_on = False

; analysis units to instantiate
use_vhf = True
use_audio = True
use_camera = True

; triggers to be evaluated
use_trigger_vhf = True
use_trigger_audio = True
use_trigger_camera = True

[CameraAnalysisUnit]
light_pin = 14

[AudioAnalysisUnit]
threshold_dbfs = 40
highpass_hz = 15000
wave_export_len_s = 300

quiet_threshold_s = 1.0
noise_threshold_s = 0.15

[VHFAnalysisUnit]
freq_center_hz = 150100001
freq_bw_hz = 8000
untrigger_duration_s = 10

; properties of the signals
sig_freqs_mhz = [150.077, 150.038, 150.225, 150.203]
sig_threshold_dbm = 50
sig_duration_threshold_s = 0.02
sig_poll_interval_s = 0.1

; properties for frequency active / passive classification 
freq_active_window_s = 60
freq_active_var = 2.0
freq_active_count = 10
```

In the configuration presented above, BatRack runs continuously 24h a day. 
All of the three analysis units (audio, vhf and camera) are evaluated and in the case that one of those detected a bat, a recording would be triggered. 

### Scheduled Operation Mode

If the `BatRack.conf` configuration file contains any `[run.*]` sections, the software runs in the *scheduled operation mode*, which allows the configuration to change over time. 
In this case BatRack runs as a daemon and instancitates varying configurations at the requested times. 

In the `[run]` sections there are two mandatory variables `start` and `stop`.
In addition to this, all variables of the `[BatRack]` section can be customized in each individual run.


```ini
; define scheduled instances with start and stop parameters
; variables defined in [run.*]-sections override those of [BatRack]
[run.1]
start = 20:00
stop = 01:00

[run.2]
start = 05:00
stop = 06:00

use_trigger_vhf = False
use_trigger_audio = False
use_trigger_camera = False
```

In the examples presented above, a trigger-based run (as defined in the `[BatRack]` section defined in the first example) would be running starting 8 pm until 1 am at night.

Second, a continuous recording would run from 5 until 6 am. 
In this second example none of the triggers are actually used (`use_trigger_* = False`), which is internally interpreted as a continuous recording.

#### Overlapping schedulings

Since some of the sensors are mutual exclusive, only one instance of BatRack is allowed to run at a time. 
This is also the case for scheduled runs.
If a run is configured to start before an ongoing job ends, it will wait for the running job to finish.

## Installation

BatRack is currently only supported on the Raspberry Pi platform, since it depends on its GPIO port interface. 
The required python libraries are defined as dependecies and are automatically installed when using `pip`.
`numpy` as well as `pyaudio` require binaray libraries, which need to be installed manually. 
In the case of Raspbian / Raspberry Pi OS, this can be achieved using `apt`:

```bash
apt-get install -y python3-numpy python3-pyaudio
python3 -m pip install .
```


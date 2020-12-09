# BatRack in a nutshell 

![Photograph of a BatRack running a recording at night.](img/batrack-city.jpg)

### Examples:

BatRack can capture videos like the following in an automatic and sensor triggered way.

<figure class="video_container">
  <video controls="true" allowfullscreen="true" width="100%">
    <source src="schwaermende_mbecs.mp4" type="video/mp4">
  </video>
</figure>

An example spectrogram of bat calls recorded by BatRack. (It was post-processed by audacity by hand with moise filter, highpass and lowpass filter)

![Spectrogram of a batcall recorded by BatRack](img/bat_calls.jpeg)

The following video shows the match between the incoming vhf signals and the recorded video.

<figure class="video_container"> 
  <video controls="true" allowfullscreen="true" width="100%">
    <source src="vhf_video_match.mp4" type="video/mp4">
  </video>
</figure>

## Parts list

To rebuild BatRack in the hole setup you have to buy the following parts. 

| Position                  | Count | Part list item | Price per unit | Price summed up |
|---------------------------|-------|----------------|----------------|-----------------|
| raspberry Pi              |     1 | A              |        40,00 € |         40,00 € |
| case (big)                |     1 |                |        35,00 € |         35,00 € |
| case (small)              |     1 |                |        25,00 € |         25,00 € |
| ultrasonic microphone     |     1 | F              |       250,00 € |        250,00 € |
| hq camera                 |     1 | H              |        55,00 € |         55,00 € |
| camera optic              |     1 | H              |        25,00 € |         25,00 € |
| relaise                   |     1 | E              |         1,40 € |          1,40 € |
| ir led                    |     1 | G              |        70,00 € |         70,00 € |
| ribbon hdmi adapter       |     1 | L              |         9,00 € |          9,00 € |
| neutrik feedthrough       |     2 | L              |        12,00 € |         24,00 € |
| neutrik hdmi cabel        |     1 |                |        39,00 € |         39,00 € |
| hdmi cable short          |     2 | L              |         3,00 € |          6,00 € |
| sdr stick                 |     1 | B              |        40,00 € |         40,00 € |
| vhf antenna               |     1 | I              |        50,00 € |         50,00 € |
| vhf cable                 |     1 |                |        15,00 € |         15,00 € |
| sma adapter               |     1 |                |         3,20 € |          3,20 € |
| 12v cigarette lighter     |     1 | D              |         7,00 € |          7,00 € |
| 12v -> 5v adapter         |     1 | D              |         7,00 € |          7,00 € |
| neutrik power plug        |     1 |                |        12,00 € |         12,00 € |
| 3D printed mounting plate |     1 |                |         3,00 € |          3,00 € |
| feedthrough led           |     1 |                |         0,50 € |          0,50 € |
| moun microphone           |     1 |                |         1,50 € |          1,50 € |
| mount camera              |     1 |                |            2,5 |          2,50 € |
| usb a -> mirco usb cable  |     1 |                |           1,25 |          1,25 € |
| rtc                       |     1 | C              |         3,20 € |          3,20 € |
| Subtotal                  |       |                |                |        725,55 € |
| tripod mount              |     1 |                |        80,00 € |         80,00 € |
| wam bam box               |     1 |                |        30,00 € |         30,00 € |
| solar charger             |     1 |                |        20,00 € |         20,00 € |
| battery                   |     1 | J              |       150,00 € |        150,00 € |
| fuse                      |     1 |                |        10,00 € |         10,00 € |
| power cable               |     1 |                |         5,00 € |          5,00 € |
| neutrik power plug        |     1 |                |        12,00 € |         12,00 € |
| solar panel               |     1 | K              |       165,00 € |        165,00 € |
|                           |       |                |                |                 |
| Total                     |       |                |                |      1.923,10 € |

With all parts you can build BatRack by following the instructions of the following steps:

Step 1:

Step 2:

Step 3:

Step 4:

# Flowchart

![Flowchart of audio and video unit](flowchart.pdf)

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
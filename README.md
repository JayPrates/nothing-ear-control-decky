# Nothing Ear Control for Decky Loader

Controls Nothing and CMF earbuds from the Quick Access menu in Steam's Game Mode (Steam Deck or any Linux PC
running Decky Loader): battery (left, right, case), noise control, EQ presets, bass enhance, in-ear detection,
low latency mode and find my earbuds.

The protocol was learned from the open-source [ear-web](https://github.com/radiance-project/ear-web) project.
Not affiliated with Nothing Technology Limited.

## Install

Needs [Decky Loader](https://decky.xyz) and `bluetoothctl` (on CachyOS/Arch: `sudo pacman -S bluez-utils`).
Pair the earbuds with the machine first, then run this as your normal user:

```sh
curl -fsSL https://raw.githubusercontent.com/JayPrates/nothing-ear-control-decky/main/install.sh | bash
```

It downloads the latest release into `~/homebrew/plugins/NothingEarControl` and restarts Decky. Run it again to update.

Manual alternative: download `NothingEarControl.zip` from [Releases](https://github.com/JayPrates/nothing-ear-control-decky/releases),
then in Decky turn on Settings → General → **Developer mode** and use Developer → **Install Plugin from ZIP File**.

## How it works

- `main.py` (backend, runs as root via the `_root` flag): lists paired devices with `bluetoothctl`, finds the
  RFCOMM channel of the Nothing control service with a small SDP client over L2CAP (Linux can't connect to a
  service by UUID), then speaks the Nothing protocol over RFCOMM. Battery is refreshed every minute. There's no need
  to press Connect: as soon as paired Nothing/CMF earbuds (or the last used device) connect to the machine, it
  connects to them by itself, retrying if they don't answer yet. After you press Disconnect it leaves them alone
  until they disconnect and reconnect. It doesn't poll: it listens for BlueZ's change signals with
  `busctl monitor` (falling back to a check every 30 s if busctl isn't available).
- `src/index.tsx` (frontend): the panel; polls the backend once a second while open.
- Logs: Decky's plugin log, plus every frame sent/received in `traffic.log` in the plugin's log folder
  (`~/homebrew/logs/Nothing Ear Control/`).

## Build

`npm install && npm run build` builds `dist/index.js`. On Windows, `build.ps1` also packages
`out/NothingEarControl.zip` (the release asset).

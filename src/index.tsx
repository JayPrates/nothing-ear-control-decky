// Nothing Ear Control — Decky Loader panel. All Bluetooth work happens in main.py; this polls its state.

import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  PanelSection,
  PanelSectionRow,
  SliderField,
  ToggleField,
  showModal,
  staticClasses,
} from "@decky/ui";
import { callable, definePlugin } from "@decky/api";
import { useEffect, useState } from "react";
import { FaHeadphones } from "react-icons/fa";

type Device = { address: string; name: string; connected: boolean };
type Battery = {
  left: number; right: number; case: number; single: number;
  leftCharging: boolean; rightCharging: boolean; caseCharging: boolean; singleCharging: boolean;
};
type State = {
  connected: boolean; connecting: boolean; status: string; error: boolean;
  device: { address: string; name: string } | null;
  model: string | null; base: string | null; firmware: string | null;
  battery: Battery; caseStale: { level: number; time: number } | null;
  anc: number | null; ancOptions: { value: number; label: string }[];
  eq: number | null; eqNote: string; hasEq: boolean;
  bassOn: boolean; bassLevel: number; hasBass: boolean; bassMax: number;
  inEar: boolean; hasInEar: boolean; latency: boolean;
  ringsAsOne: boolean; ringingLeft: boolean; ringingRight: boolean;
  lastDevice: string | null;
};

const getState = callable<[], State>("get_state");
const listDevices = callable<[], Device[]>("list_devices");
const connect = callable<[mac: string], void>("connect");
const disconnect = callable<[], void>("disconnect");
const setAnc = callable<[wire: number], void>("set_anc");
const setEq = callable<[preset: number], void>("set_eq");
const setBass = callable<[on: boolean, level: number], void>("set_bass");
const setInEar = callable<[on: boolean], void>("set_in_ear");
const setLatency = callable<[on: boolean], void>("set_latency");
const ring = callable<[side: string, on: boolean], void>("ring");

const EQ_PRESETS = [
  { data: 0, label: "Balanced" },
  { data: 3, label: "More bass" },
  { data: 2, label: "More treble" },
  { data: 1, label: "Voice" },
];

const muted = "#8b929a";

function formatWhen(epochSeconds: number) {
  const t = new Date(epochSeconds * 1000);
  const today = new Date();
  const yesterday = new Date(Date.now() - 86400000);
  if (t.toDateString() === today.toDateString())
    return t.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (t.toDateString() === yesterday.toDateString()) return "yesterday";
  return t.toLocaleDateString([], { day: "numeric", month: "short" });
}

function Gauge({ label, level, charging, note }: { label: string; level: number; charging: boolean; note?: string }) {
  const known = level >= 0;
  const stale = note !== undefined;
  const color = !known || stale ? muted : charging ? "#46d478" : level <= 15 ? "#ff7068" : "#ffffff";
  return (
    <div style={{ flex: 1, textAlign: "center", minWidth: 0 }}>
      <div style={{ fontSize: "20px", fontWeight: 600, color }}>{known ? `${level}%` : "—"}</div>
      <div style={{ height: "4px", margin: "4px 10px", borderRadius: "2px", background: "#3d4450" }}>
        <div style={{ height: "100%", width: `${known ? level : 0}%`, borderRadius: "2px", background: color }} />
      </div>
      <div style={{ fontSize: "12px", color: charging ? "#46d478" : muted }}>{label}{charging ? " · charging" : ""}</div>
      {stale && <div style={{ fontSize: "11px", color: muted }}>{note}</div>}
    </div>
  );
}

function BatteryRow({ s }: { s: State }) {
  const b = s.battery;
  if (b.single >= 0)
    return <Gauge label="Battery" level={b.single} charging={b.singleCharging} />;
  const stale = b.case < 0 && s.caseStale ? s.caseStale : null;
  return (
    <div style={{ display: "flex", padding: "6px 0" }}>
      <Gauge label="Left" level={b.left} charging={b.leftCharging} />
      <Gauge label="Right" level={b.right} charging={b.rightCharging} />
      {stale
        ? <Gauge label="Case" level={stale.level} charging={false} note={"last seen " + formatWhen(stale.time)} />
        : <Gauge label="Case" level={b.case} charging={b.caseCharging} />}
    </div>
  );
}

function Note({ text, error }: { text: string; error?: boolean }) {
  if (!text) return null;
  return (
    <PanelSectionRow>
      <div style={{ fontSize: "12px", lineHeight: "16px", color: error ? "#ff7068" : muted, padding: "2px 0 6px" }}>{text}</div>
    </PanelSectionRow>
  );
}

function confirmRing(what: string, onOK: () => void) {
  showModal(
    <ConfirmModal
      strTitle="Find my earbuds"
      strDescription={`Ring ${what}? It plays a loud sound. Make sure nobody is wearing them.`}
      strOKButtonText="Ring"
      onOK={onOK}
    />
  );
}

function Content() {
  const [s, setS] = useState<State | null>(null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [picked, setPicked] = useState<string | null>(null);

  const refresh = async () => {
    try { setS(await getState()); } catch (e) { console.error("Ear Control", e); }
  };
  const refreshDevices = async () => {
    try { setDevices(await listDevices()); } catch (e) { console.error("Ear Control", e); }
  };

  useEffect(() => {
    refresh();
    refreshDevices();
    const timer = setInterval(refresh, 1000);
    return () => clearInterval(timer);
  }, []);

  // After a command, show the change right away instead of waiting for the next poll.
  const act = async (f: () => Promise<void>) => { await f(); await refresh(); };

  if (!s) return <PanelSection><Note text="Loading…" /></PanelSection>;

  if (!s.connected) {
    const selected = picked ?? (devices.some((d) => d.address === s.lastDevice) ? s.lastDevice : devices[0]?.address ?? null);
    const device = devices.find((d) => d.address === selected);
    return (
      <PanelSection title="Earbuds">
        {devices.length > 0 ? (
          <PanelSectionRow>
            <DropdownItem
              label="Device"
              description={device ? (device.connected ? "Connected to this Deck" : "Paired") : undefined}
              rgOptions={devices.map((d) => ({ data: d.address, label: d.name }))}
              selectedOption={selected}
              onChange={(o) => setPicked(o.data)}
            />
          </PanelSectionRow>
        ) : (
          <Note text="No paired audio devices. Pair your earbuds in Settings → Bluetooth, then refresh." />
        )}
        {devices.length > 0 && (
          <PanelSectionRow>
            <ButtonItem layout="below" disabled={s.connecting || !selected} onClick={() => selected && act(() => connect(selected))}>
              {s.connecting ? "Connecting…" : "Connect"}
            </ButtonItem>
          </PanelSectionRow>
        )}
        <Note
          text={s.status || (device ? `Take ${device.name} out of the case, then press Connect.` : "")}
          error={s.error}
        />
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={s.connecting} onClick={refreshDevices}>Refresh devices</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const one = s.ringsAsOne;
  const b = s.battery;
  const leftOnline = b.single >= 0 || b.left >= 0;
  const rightOnline = b.single >= 0 || b.right >= 0;
  const ringing = s.ringingLeft || s.ringingRight;
  const subtitle = [s.firmware ? "Firmware " + s.firmware : s.base ? "Reading settings…" : "Unknown model — showing common controls", s.base]
    .filter(Boolean).join(" · ");

  return (
    <>
      <PanelSection title={s.model ?? s.device?.name ?? "Earbuds"}>
        <Note text={subtitle} />
        <PanelSectionRow><BatteryRow s={s} /></PanelSectionRow>
        {b.single < 0 && b.case < 0 && <Note text="The case reports its battery only while an earbud is inside it." />}
      </PanelSection>

      {(s.ancOptions.length > 0 || s.hasEq || s.hasBass) && (
        <PanelSection title="Sound">
          {s.ancOptions.length > 0 && (
            <PanelSectionRow>
              <DropdownItem
                label="Noise control"
                rgOptions={s.ancOptions.map((o) => ({ data: o.value, label: o.label }))}
                selectedOption={s.anc}
                onChange={(o) => act(() => setAnc(o.data))}
              />
            </PanelSectionRow>
          )}
          {s.hasEq && (
            <PanelSectionRow>
              <DropdownItem
                label="Equalizer"
                rgOptions={EQ_PRESETS}
                selectedOption={s.eq}
                strDefaultLabel="Custom"
                onChange={(o) => act(() => setEq(o.data))}
              />
            </PanelSectionRow>
          )}
          {s.hasEq && <Note text={s.eqNote} />}
          {s.hasBass && (
            <PanelSectionRow>
              <ToggleField
                label="Bass enhance"
                description="Deeper, fuller low end"
                checked={s.bassOn}
                onChange={(on) => act(() => setBass(on, s.bassLevel))}
              />
            </PanelSectionRow>
          )}
          {s.hasBass && s.bassOn && (
            <PanelSectionRow>
              <SliderField
                label="Bass level"
                value={s.bassLevel}
                min={1}
                max={s.bassMax}
                step={1}
                notchCount={s.bassMax}
                notchTicksVisible
                showValue
                onChange={(v) => { if (v !== s.bassLevel) act(() => setBass(true, v)); }}
              />
            </PanelSectionRow>
          )}
        </PanelSection>
      )}

      <PanelSection title="Settings">
        {s.hasInEar && (
          <PanelSectionRow>
            <ToggleField
              label="In-ear detection"
              description="Pause audio when you take an earbud out"
              checked={s.inEar}
              onChange={(on) => act(() => setInEar(on))}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <ToggleField
            label="Low latency mode"
            description="Less audio delay for games and video"
            checked={s.latency}
            onChange={(on) => act(() => setLatency(on))}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Find my earbuds">
        <Note text={
          "Plays a loud sound. Earbuds stay silent if they think they're being worn." +
          (!one && !leftOnline && !rightOnline ? " Neither earbud is reporting in — take them out of the case." :
            !one && !leftOnline ? " The left earbud isn't connected (in the case?)." :
              !one && !rightOnline ? " The right earbud isn't connected (in the case?)." : "")
        } />
        {ringing ? (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => act(() => ring("both", false))}>Stop ringing</ButtonItem>
          </PanelSectionRow>
        ) : one ? (
          <PanelSectionRow>
            <ButtonItem layout="below" onClick={() => confirmRing("your earbuds", () => act(() => ring("left", true)))}>Ring</ButtonItem>
          </PanelSectionRow>
        ) : (
          <>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={!leftOnline} onClick={() => confirmRing("the left earbud", () => act(() => ring("left", true)))}>Ring left</ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={!rightOnline} onClick={() => confirmRing("the right earbud", () => act(() => ring("right", true)))}>Ring right</ButtonItem>
            </PanelSectionRow>
            <PanelSectionRow>
              <ButtonItem layout="below" disabled={!leftOnline || !rightOnline} onClick={() => confirmRing("both earbuds", () => act(() => ring("both", true)))}>Ring both</ButtonItem>
            </PanelSectionRow>
          </>
        )}
      </PanelSection>

      <PanelSection>
        <Note text={s.status} error={s.error} />
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => act(disconnect)}>Disconnect</ButtonItem>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
}

export default definePlugin(() => ({
  name: "Nothing Ear Control",
  titleView: <div className={staticClasses.Title}>Ear Control</div>,
  content: <Content />,
  icon: <FaHeadphones />,
}));

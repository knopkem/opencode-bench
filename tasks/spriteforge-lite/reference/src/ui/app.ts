import { newDocument } from "../model/document";
import { History } from "../model/history";
import type { DocState, Frame, Layer } from "../model/document";
import { W, H, TRANSPARENT, MIN_FPS, MAX_FPS, MAX_FRAMES, clamp } from "../model/constants";
import { linePixels, rectPixels, ellipsePixels, brushPixels, floodFill, eyedrop } from "../model/tools";
import { compositeWithOnion } from "../model/composite";
import { serializeProject, deserializeProject } from "../model/project";
import { PRESETS } from "../model/palette";
import { paintComposite } from "./render";

const SCALE = 16; // 32 * 16 = 512
const VIEW = W * SCALE;
type Tool = "pencil" | "eraser" | "line" | "rect" | "ellipse" | "fill" | "eyedropper";
const TOOLS: [Tool, string, string][] = [
  ["pencil", "Pencil", "B"],
  ["eraser", "Eraser", "E"],
  ["line", "Line", "L"],
  ["rect", "Rectangle", "R"],
  ["ellipse", "Ellipse", "O"],
  ["fill", "Bucket Fill", "G"],
  ["eyedropper", "Eyedropper", "I"],
];

export class App {
  doc = newDocument();
  history = new History();
  pending: DocState | null = null;
  tool: Tool = "pencil";
  brush: 1 | 2 = 1;
  colorIndex = 0;
  onion = 30; // percent
  playing = false;
  timer: number | null = null;
  dragFrom: { x: number; y: number } | null = null;
  dragButton = 0;

  els: Record<string, HTMLElement> = {};

  init(): void {
    const saved = localStorage.getItem("spriteforge.project");
    if (saved) {
      try { this.doc.state = deserializeProject(saved); } catch { /* start fresh */ }
    }
    this.buildDom();
    this.bindEvents();
    this.renderAll();
    this.installTestApi();
  }

  // ---------- test contract ----------

  installTestApi(): void {
    (window as unknown as { __spriteforge: unknown }).__spriteforge = {
      getState: () => this.getState(),
      loadState: (state: unknown) => this.loadState(state),
      reset: () => this.resetDocument(),
    };
  }

  getState(): unknown {
    const st = this.doc.state;
    const frame = st.frames[st.activeFrame];
    const activeLayer = Math.max(
      0,
      frame.layers.findIndex((layer) => layer.id === st.activeLayerId),
    );
    return {
      width: st.width,
      height: st.height,
      fps: st.fps,
      loop: st.loop,
      palette: [...st.palette],
      activeFrame: st.activeFrame,
      activeLayer,
      tool: this.tool,
      brush: this.brush,
      frames: st.frames.map((f) => ({
        hold: f.hold,
        layers: f.layers.map((l) => ({
          name: l.name,
          visible: l.visible,
          opacity: l.opacity,
          pixels: Array.from(l.pixels),
        })),
      })),
    };
  }

  loadState(raw: unknown): void {
    const state = raw as {
      fps: number;
      loop: boolean;
      palette: string[];
      activeFrame: number;
      activeLayer: number;
      tool: string;
      brush: 1 | 2;
      frames: Array<{ hold: number; layers: Array<{ name: string; visible: boolean; opacity: number; pixels: number[] }> }>;
    };
    let nextId = 1;
    const frames: Frame[] = state.frames.map((f) => ({
      hold: f.hold,
      layers: f.layers.map((l): Layer => ({
        id: nextId++,
        name: l.name,
        visible: l.visible,
        opacity: l.opacity,
        pixels: Int16Array.from(l.pixels),
      })),
    }));
    const activeFrame = clamp(state.activeFrame, 0, frames.length - 1);
    const activeLayer = clamp(state.activeLayer, 0, frames[activeFrame].layers.length - 1);
    this.doc.state = {
      width: W,
      height: H,
      fps: clamp(Math.round(state.fps), MIN_FPS, MAX_FPS),
      loop: state.loop,
      palette: [...state.palette],
      frames,
      activeFrame,
      activeLayerId: frames[activeFrame].layers[activeLayer].id,
      nextLayerId: nextId,
    };
    this.history.clear();
    this.tool = (["pencil", "eraser", "line", "rect", "ellipse", "fill", "eyedropper"].includes(state.tool)
      ? state.tool
      : "pencil") as Tool;
    this.brush = state.brush === 2 ? 2 : 1;
    this.autosave();
    this.renderAll();
  }

  resetDocument(): void {
    localStorage.removeItem("spriteforge.project");
    this.doc = newDocument();
    this.history.clear();
    this.tool = "pencil";
    this.brush = 1;
    this.renderAll();
    this.autosave();
  }

  exportPng(): void {
    const st = this.doc.state;
    const data = compositeWithOnion(st, st.activeFrame, null, 0);
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.putImageData(new ImageData(data, W, H), 0, 0);
    const link = document.createElement("a");
    link.download = "spriteforge.png";
    link.href = canvas.toDataURL("image/png");
    link.click();
  }

  // ---------- persistence & history ----------

  autosave(): void {
    try { localStorage.setItem("spriteforge.project", serializeProject(this.doc.state)); } catch { /* ignore */ }
  }

  beginEdit(): void {
    this.pending = this.doc.snapshot();
  }

  commit(): void {
    if (this.pending) {
      this.history.push(this.pending);
      this.pending = null;
    }
    this.autosave();
    this.renderAll();
  }

  safeOp(fn: () => void): void {
    try { fn(); this.commit(); } catch (e) { this.status(e instanceof Error ? e.message : String(e)); }
  }

  undo(): void {
    const s = this.history.undo(this.doc.snapshot());
    if (!s) { this.status("nothing to undo"); return; }
    this.doc.restore(s);
    this.autosave();
    this.renderAll();
    this.status(`undo · ${this.history.undoCount()} left`);
  }

  redo(): void {
    const s = this.history.redo(this.doc.snapshot());
    if (!s) { this.status("nothing to redo"); return; }
    this.doc.restore(s);
    this.autosave();
    this.renderAll();
    this.status("redo");
  }

  status(msg: string): void {
    (this.els.status as HTMLElement).textContent = msg;
  }

  // ---------- DOM ----------

  buildDom(): void {
    const app = document.getElementById("app")!;
    app.innerHTML = "";

    // left column: toolbar + canvas
    const toolbar = el("div", "toolbar");
    toolbar.appendChild(el("h3", "", "Tools"));
    for (const [tool, label, key] of TOOLS) {
      const b = btn(`${label} (${key})`, `tool-${tool}`);
      b.dataset.tool = tool;
      b.dataset.testid = `tool-${tool}`;
      b.addEventListener("click", () => {
        this.setTool(tool);
        (this.els.canvaswrap as HTMLElement).focus();
      });
      toolbar.appendChild(b);
    }
    const brushRow = el("div", "brushrow");
    brushRow.append(el("span", "hint", "Brush: "));
    const bs = el("span", "brushsize", `${this.brush}px`);
    bs.id = "brushsize";
    brushRow.appendChild(bs);
    const b1 = btn("1px", ""); b1.className = "smallbtn"; b1.dataset.testid = "brush-1";
    b1.addEventListener("click", () => { this.brush = 1; this.renderToolbar(); (this.els.canvaswrap as HTMLElement).focus(); });
    const b2 = btn("2px", ""); b2.className = "smallbtn"; b2.dataset.testid = "brush-2";
    b2.addEventListener("click", () => { this.brush = 2; this.renderToolbar(); (this.els.canvaswrap as HTMLElement).focus(); });
    brushRow.append(b1, b2, el("span", "hint", "[ / ]"));
    toolbar.appendChild(brushRow);

    const editRow = el("div", "editrow");
    const undoB = btn("Undo Ctrl+Z", ""); undoB.className = "smallbtn"; undoB.dataset.testid = "undo";
    undoB.addEventListener("click", () => { this.undo(); });
    const redoB = btn("Redo Ctrl+Shift+Z", ""); redoB.className = "smallbtn"; redoB.dataset.testid = "redo";
    redoB.addEventListener("click", () => { this.redo(); });
    const exportB = btn("Export PNG", ""); exportB.className = "smallbtn"; exportB.dataset.testid = "export";
    exportB.addEventListener("click", () => this.exportPng());
    editRow.append(undoB, redoB, exportB);
    toolbar.appendChild(editRow);

    const wrap = el("div", "canvaswrap");
    wrap.tabIndex = 0;
    const main = document.createElement("canvas");
    main.width = VIEW; main.height = VIEW;
    main.id = "main-canvas";
    main.dataset.testid = "canvas";
    const overlay = document.createElement("canvas");
    overlay.width = VIEW; overlay.height = VIEW;
    overlay.id = "overlay";
    wrap.append(main, overlay);
    const statusDiv = el("div", "status", "");
    statusDiv.dataset.testid = "status";
    const center = el("div", "centercol");
    center.append(wrap, statusDiv);
    this.els.main = main; this.els.overlay = overlay; this.els.canvaswrap = wrap; this.els.status = statusDiv;

    // right column: palette + layers + playback settings
    const right = el("div", "rightcol");

    const palPanel = el("div", "panel");
    palPanel.appendChild(el("h3", "", "Palette"));
    const presetSel = document.createElement("select");
    presetSel.id = "preset";
    for (const p of PRESETS) {
      const o = document.createElement("option");
      o.value = p.name; o.textContent = p.name;
      presetSel.appendChild(o);
    }
    presetSel.addEventListener("change", () => {
      const p = PRESETS.find((x) => x.name === presetSel.value);
      if (p) this.safeOp(() => { this.beginEdit(); this.doc.applyPreset(p.colors); });
      (this.els.canvaswrap as HTMLElement).focus();
    });
    palPanel.appendChild(presetSel);
    const swatches = el("div", "swatches");
    palPanel.appendChild(swatches);
    this.els.swatches = swatches;

    const curRow = el("div", "curcolor-row");
    const curSwatch = el("div", "cur-swatch");
    const curHex = el("span", "cur-hex", "");
    curRow.append(curSwatch, curHex);
    palPanel.appendChild(curRow);
    this.els.curSwatch = curSwatch; this.els.curHex = curHex;

    const picker = document.createElement("input");
    picker.type = "color";
    picker.id = "picker";
    picker.addEventListener("input", () => {
      (this.els.curHex as HTMLElement).textContent = picker.value;
      (this.els.curSwatch as HTMLElement).style.background = picker.value;
    });
    const applyBtn = btn("Apply to slot", ""); applyBtn.className = "smallbtn";
    applyBtn.addEventListener("click", () => {
      this.safeOp(() => { this.beginEdit(); this.doc.setColor(this.selectedSlot(), picker.value); });
    });
    const addBtn = btn("Add color", ""); addBtn.className = "smallbtn";
    addBtn.addEventListener("click", () => {
      this.safeOp(() => {
        this.beginEdit();
        const idx = this.doc.addColor(picker.value);
        this.colorIndex = idx;
      });
    });
    const removeBtn = btn("Remove", ""); removeBtn.className = "smallbtn";
    removeBtn.addEventListener("click", () => {
      this.safeOp(() => {
        this.beginEdit();
        const i = this.selectedSlot();
        this.doc.removeColor(i);
        this.colorIndex = clamp(this.colorIndex, 0, this.doc.state.palette.length - 1);
      });
    });
    const leftB = btn("◀", ""); leftB.className = "smallbtn";
    leftB.addEventListener("click", () => {
      this.safeOp(() => {
        this.beginEdit();
        const i = this.selectedSlot();
        if (i > 0) { this.doc.moveColor(i, i - 1); this.colorIndex = i - 1; }
      });
    });
    const rightB = btn("▶", ""); rightB.className = "smallbtn";
    rightB.addEventListener("click", () => {
      this.safeOp(() => {
        this.beginEdit();
        const i = this.selectedSlot();
        if (i < this.doc.state.palette.length - 1) { this.doc.moveColor(i, i + 1); this.colorIndex = i + 1; }
      });
    });
    const palBtns = el("div", "palrow");
    palBtns.append(picker, applyBtn, addBtn, removeBtn, leftB, rightB);
    palPanel.appendChild(palBtns);
    right.appendChild(palPanel);

    const layerPanel = el("div", "panel");
    layerPanel.appendChild(el("h3", "", "Layers"));
    const layerList = el("div", "layerlist");
    layerPanel.appendChild(layerList);
    this.els.layerlist = layerList;
    const lRow = el("div", "palrow");
    const addL = btn("+ Add", ""); addL.className = "smallbtn"; addL.dataset.testid = "layer-add";
    addL.addEventListener("click", () => this.safeOp(() => { this.beginEdit(); this.doc.addLayer(); }));
    const delL = btn("Delete", ""); delL.className = "smallbtn"; delL.dataset.testid = "layer-delete";
    delL.addEventListener("click", () => this.safeOp(() => { this.beginEdit(); this.doc.deleteLayer(this.doc.state.activeLayerId); }));
    const upL = btn("▲", ""); upL.className = "smallbtn";
    upL.addEventListener("click", () => this.moveActiveLayer(+1));
    const dnL = btn("▼", ""); dnL.className = "smallbtn";
    dnL.addEventListener("click", () => this.moveActiveLayer(-1));
    lRow.append(addL, delL, upL, dnL, el("span", "hint", "(dbl-click name to rename · drag rows)"));
    layerPanel.appendChild(lRow);
    right.appendChild(layerPanel);

    const playPanel = el("div", "panel");
    playPanel.appendChild(el("h3", "", "Playback & Onion Skin"));
    const fpsRow = el("label", "row");
    const fpsInput = document.createElement("input");
    fpsInput.type = "number"; fpsInput.min = String(MIN_FPS); fpsInput.max = String(MAX_FPS); fpsInput.id = "fps";
    fpsInput.dataset.testid = "fps";
    fpsRow.append(el("span", "lbl", "FPS (" + MIN_FPS + "–" + MAX_FPS + ")"), fpsInput);
    fpsInput.addEventListener("change", () => this.safeOp(() => { this.beginEdit(); this.doc.setFps(Number(fpsInput.value)); }));
    this.els.fps = fpsInput;

    const loopRow = el("label", "row");
    const loopChk = document.createElement("input");
    loopChk.type = "checkbox"; loopChk.id = "loop";
    loopRow.append(loopChk, el("span", "lbl", "Loop"));
    loopChk.addEventListener("change", () => this.safeOp(() => { this.beginEdit(); this.doc.setLoop(loopChk.checked); }));
    this.els.loop = loopChk;

    const onionRow = el("label", "row");
    const onionSlider = document.createElement("input");
    onionSlider.type = "range"; onionSlider.min = "0"; onionSlider.max = "100"; onionSlider.value = String(this.onion);
    onionSlider.id = "onion";
    const onionVal = el("span", "lbl", `${this.onion}%`);
    onionRow.append(el("span", "lbl", "Onion skin (prev frame):"), onionSlider, onionVal);
    onionSlider.addEventListener("input", () => {
      this.onion = Number(onionSlider.value);
      onionVal.textContent = `${this.onion}%`;
      this.renderCanvas();
    });
    playPanel.append(fpsRow, loopRow, onionRow);
    right.appendChild(playPanel);

    // bottom: transport + filmstrip
    const bottom = el("div", "bottom");
    const transport = el("div", "transport");
    const playB = btn("▶ Play / Pause (Space)", ""); playB.className = "smallbtn tbtn"; playB.dataset.testid = "play";
    playB.addEventListener("click", () => { this.togglePlay(); (this.els.canvaswrap as HTMLElement).focus(); });
    const pauseB = btn("⏸ Pause", ""); pauseB.className = "smallbtn tbtn"; pauseB.dataset.testid = "pause";
    pauseB.addEventListener("click", () => { this.pause(); (this.els.canvaswrap as HTMLElement).focus(); });
    const stopB = btn("⏹ Stop", ""); stopB.className = "smallbtn tbtn"; stopB.dataset.testid = "stop";
    stopB.addEventListener("click", () => { this.stop(); (this.els.canvaswrap as HTMLElement).focus(); });
    const backB = btn("← Step (←)", ""); backB.className = "smallbtn tbtn"; backB.dataset.testid = "step-back";
    backB.addEventListener("click", () => { this.step(-1); (this.els.canvaswrap as HTMLElement).focus(); });
    const fwdB = btn("→ Step (→)", ""); fwdB.className = "smallbtn tbtn"; fwdB.dataset.testid = "step-forward";
    fwdB.addEventListener("click", () => { this.step(1); (this.els.canvaswrap as HTMLElement).focus(); });
    const holdLbl = el("span", "lbl", "Hold:");
    const holdInput = document.createElement("input");
    holdInput.type = "number"; holdInput.min = "1"; holdInput.max = "24"; holdInput.id = "hold";
    holdInput.addEventListener("change", () => {
      this.safeOp(() => { this.beginEdit(); this.doc.setFrameHold(this.doc.state.activeFrame, Number(holdInput.value)); });
    });
    this.els.hold = holdInput;
    transport.append(stopB, backB, playB, pauseB, fwdB, holdLbl, holdInput,
      el("span", "hint", `frame ${this.doc.state.activeFrame + 1}/${MAX_FRAMES} max · click thumbnails to scrub`));
    bottom.appendChild(transport);

    const stripBtns = el("div", "palrow");
    const addF = btn("+ Add frame", ""); addF.className = "smallbtn"; addF.dataset.testid = "frame-add";
    addF.addEventListener("click", () => this.safeOp(() => { this.beginEdit(); this.doc.addFrame(); }));
    const dupF = btn("Duplicate", ""); dupF.className = "smallbtn"; dupF.dataset.testid = "frame-duplicate";
    dupF.addEventListener("click", () => this.safeOp(() => { this.beginEdit(); this.doc.duplicateFrame(); }));
    const delF = btn("Delete frame", ""); delF.className = "smallbtn"; delF.dataset.testid = "frame-delete";
    delF.addEventListener("click", () => this.safeOp(() => { this.beginEdit(); this.doc.deleteFrame(this.doc.state.activeFrame); }));
    const mlF = btn("◀ Move frame", ""); mlF.className = "smallbtn";
    mlF.addEventListener("click", () => this.moveActiveFrame(-1));
    const mrF = btn("Move frame ▶", ""); mrF.className = "smallbtn";
    mrF.addEventListener("click", () => this.moveActiveFrame(+1));
    stripBtns.append(addF, dupF, delF, mlF, mrF, el("span", "hint", "(drag thumbnails to reorder)"));
    bottom.appendChild(stripBtns);

    const strip = el("div", "filmstrip");
    bottom.appendChild(strip);
    this.els.strip = strip;

    app.append(toolbar, center, right, bottom);
  }

  selectedSlot(): number {
    return clamp(this.colorIndex, 0, this.doc.state.palette.length - 1);
  }

  moveActiveLayer(dir: number): void {
    const layers = this.doc.activeFrame_().layers;
    const i = layers.findIndex((l) => l.id === this.doc.state.activeLayerId);
    const j = i + dir;
    if (j < 0 || j >= layers.length) { this.status("layer already at that position"); return; }
    this.safeOp(() => { this.beginEdit(); this.doc.moveLayer(i, j); });
  }

  moveActiveFrame(dir: number): void {
    const i = this.doc.state.activeFrame;
    const j = i + dir;
    if (j < 0 || j >= this.doc.state.frames.length) { this.status("frame already at that position"); return; }
    this.safeOp(() => { this.beginEdit(); this.doc.moveFrame(i, j); });
  }

  setTool(t: Tool): void {
    this.tool = t;
    this.renderToolbar();
    this.status(`tool: ${t}`);
  }

  // ---------- events ----------

  bindEvents(): void {
    const wrap = this.els.canvaswrap as HTMLElement;

    wrap.addEventListener("pointerdown", (e) => {
      wrap.focus();
      const p = this.px(e.clientX, e.clientY);
      if (!p) return;
      this.dragButton = e.button;
      if (this.tool === "eyedropper") {
        const v = eyedrop(this.doc.activeLayer()!.pixels, p.x, p.y);
        if (v !== TRANSPARENT) { this.colorIndex = v; this.renderAll(); this.status(`picked color slot ${v}`); }
        return;
      }
      if (this.tool === "fill") {
        this.safeOp(() => {
          this.beginEdit();
          const value = e.button === 2 ? TRANSPARENT : this.selectedSlot();
          const out = floodFill(this.doc.activeLayer()!.pixels, p.x, p.y, value);
          this.doc.drawPixels(this.doc.activeLayer()!, out, value);
        });
        return;
      }
      this.beginEdit();
      this.dragFrom = p;
      if (this.tool === "pencil" || this.tool === "eraser") {
        this.paintStroke(p);
      }
    });
    window.addEventListener("pointermove", (e) => {
      if (!this.dragFrom) return;
      const p = this.px(e.clientX, e.clientY);
      if (!p) return;
      if (this.tool === "pencil" || this.tool === "eraser") {
        const seg = linePixels(this.dragFrom.x, this.dragFrom.y, p.x, p.y);
        for (const q of seg) this.paintPoint(q);
        this.dragFrom = p;
        this.renderCanvas();
      } else {
        this.drawShapePreview(this.dragFrom, p);
      }
    });
    window.addEventListener("pointerup", (e) => {
      if (!this.dragFrom) return;
      const p = this.px(e.clientX, e.clientY) ?? this.dragFrom;
      const start = this.dragFrom;
      this.dragFrom = null;
      const erase = this.tool === "eraser" || e.button === 2;
      const value = erase ? TRANSPARENT : this.selectedSlot();
      const clearOverlay = () => {
        const octx = (this.els.overlay as HTMLCanvasElement).getContext("2d")!;
        octx.clearRect(0, 0, VIEW, VIEW);
      };
      if (this.tool === "pencil" || this.tool === "eraser") {
        this.commit();
        clearOverlay();
        return;
      }
      let pos: { x: number; y: number }[] = [];
      if (this.tool === "line") pos = linePixels(start.x, start.y, p.x, p.y);
      if (this.tool === "rect") pos = rectPixels(start.x, start.y, p.x, p.y);
      if (this.tool === "ellipse") pos = ellipsePixels(start.x, start.y, p.x, p.y);
      this.doc.drawPixels(this.doc.activeLayer()!, pos, value);
      clearOverlay();
      this.commit();
    });
    wrap.addEventListener("contextmenu", (e) => e.preventDefault());

    wrap.addEventListener("keydown", (e) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA")) return;
      const k = e.key.toLowerCase();
      const mod = e.ctrlKey || e.metaKey;
      if (mod && k === "z") {
        e.preventDefault();
        if (e.shiftKey) this.redo(); else this.undo();
        return;
      }
      if (mod) return;
      switch (k) {
        case "b": this.setTool("pencil"); break;
        case "e": this.setTool("eraser"); break;
        case "l": this.setTool("line"); break;
        case "r": this.setTool("rect"); break;
        case "o": this.setTool("ellipse"); break;
        case "g": this.setTool("fill"); break;
        case "i": this.setTool("eyedropper"); break;
        case "[": this.brush = this.brush === 1 ? 2 : 1; this.renderToolbar(); break;
        case "]": this.brush = this.brush === 1 ? 2 : 1; this.renderToolbar(); break;
        case " ": e.preventDefault(); this.togglePlay(); break;
        case "arrowleft": e.preventDefault(); this.step(-1); break;
        case "arrowright": e.preventDefault(); this.step(1); break;
        default: return;
      }
    });
  }

  px(clientX: number, clientY: number): { x: number; y: number } | null {
    const r = (this.els.main as HTMLCanvasElement).getBoundingClientRect();
    const x = Math.floor((clientX - r.left) * W / r.width);
    const y = Math.floor((clientY - r.top) * H / r.height);
    if (x < 0 || y < 0 || x >= W || y >= H) return null;
    return { x, y };
  }

  paintPoint(p: { x: number; y: number }): void {
    const value = this.tool === "eraser" || this.dragButton === 2 ? TRANSPARENT : this.selectedSlot();
    this.doc.drawPixels(this.doc.activeLayer()!, brushPixels(p.x, p.y, this.brush), value);
    this.renderCanvas();
  }

  paintStroke(p: { x: number; y: number }): void {
    this.paintPoint(p);
  }

  drawShapePreview(a: { x: number; y: number }, b: { x: number; y: number }): void {
    let pos: { x: number; y: number }[] = [];
    if (this.tool === "line") pos = linePixels(a.x, a.y, b.x, b.y);
    if (this.tool === "rect") pos = rectPixels(a.x, a.y, b.x, b.y);
    if (this.tool === "ellipse") pos = ellipsePixels(a.x, a.y, b.x, b.y);
    const overlay = this.els.overlay as HTMLCanvasElement;
    const ctx = overlay.getContext("2d")!;
    ctx.clearRect(0, 0, VIEW, VIEW);
    const erase = this.dragButton === 2;
    ctx.fillStyle = erase ? "#ffffff88" : this.doc.state.palette[this.selectedSlot()];
    for (const q of pos) ctx.fillRect(q.x * SCALE, q.y * SCALE, SCALE, SCALE);
  }

  // ---------- playback ----------

  togglePlay(): void {
    if (this.playing) { this.pause(); return; }
    this.playing = true;
    this.status(`playing at ${this.doc.state.fps} fps`);
    this.scheduleTick();
  }

  pause(): void {
    this.playing = false;
    if (this.timer !== null) { clearTimeout(this.timer); this.timer = null; }
    this.status("paused");
  }

  stop(): void {
    this.pause();
    this.doc.setActiveFrame(0);
    this.renderAll();
    this.status("stopped — frame 1");
  }

  step(dir: number): void {
    const n = this.doc.state.frames.length;
    const next = clamp(this.doc.state.activeFrame + dir, 0, n - 1);
    this.doc.setActiveFrame(next);
    this.renderAll();
    this.status(`frame ${next + 1}`);
  }

  scheduleTick(): void {
    if (!this.playing) return;
    const f = this.doc.state.frames[this.doc.state.activeFrame];
    const ms = Math.max(40, (f.hold / this.doc.state.fps) * 1000);
    this.timer = window.setTimeout(() => {
      const frames = this.doc.state.frames.length;
      const next = this.doc.state.activeFrame + 1;
      if (next >= frames) {
        if (this.doc.state.loop) {
          this.doc.setActiveFrame(0);
        } else {
          this.doc.setActiveFrame(frames - 1);
          this.pause();
          this.renderAll();
          return;
        }
      } else {
        this.doc.setActiveFrame(next);
      }
      this.renderAll();
      this.scheduleTick();
    }, ms);
  }

  // ---------- rendering ----------

  renderCanvas(): void {
    const st = this.doc.state;
    const prevIdx = st.activeFrame > 0 && this.onion > 0 ? st.activeFrame - 1 : null;
    const data = compositeWithOnion(st, st.activeFrame, prevIdx, this.onion / 100);
    paintComposite(this.els.main as HTMLCanvasElement, data);
  }

  renderToolbar(): void {
    for (const b of document.querySelectorAll<HTMLButtonElement>(".tool-btn")) {
      const active = b.dataset.tool === this.tool;
      b.classList.toggle("active", active);
      b.dataset.active = active ? "true" : "false";
    }
    const bs = document.getElementById("brushsize");
    if (bs) bs.textContent = `${this.brush}px`;
    for (const brush of [1, 2]) {
      const button = document.querySelector<HTMLButtonElement>(`[data-testid="brush-${brush}"]`);
      if (button) button.dataset.active = this.brush === brush ? "true" : "false";
    }
  }

  renderPalette(): void {
    const sw = this.els.swatches;
    sw.innerHTML = "";
    const sel = this.selectedSlot();
    this.doc.state.palette.forEach((hex, i) => {
      const s = el("button", "swatch" + (i === sel ? " selected" : ""));
      s.style.background = hex;
      s.title = `slot ${i} · ${hex}`;
      s.dataset.testid = `color-${i}`;
      s.dataset.active = i === sel ? "true" : "false";
      s.addEventListener("click", () => {
        this.colorIndex = i;
        this.renderAll();
      });
      sw.appendChild(s);
    });
    const activeHex = this.doc.state.palette[sel] ?? "#000000";
    (this.els.curSwatch as HTMLElement).style.background = activeHex;
    (this.els.curHex as HTMLElement).textContent = `active: slot ${sel} ${activeHex}`;
    const picker = document.getElementById("picker") as HTMLInputElement;
    picker.value = /^#[0-9a-f]{6}$/.test(activeHex) ? activeHex : "#000000";
  }

  renderLayers(): void {
    const list = this.els.layerlist;
    list.innerHTML = "";
    const layers = this.doc.activeFrame_().layers; // index 0 = bottom
    const n = layers.length;
    for (let row = 0; row < n; row++) {
      const arrIdx = n - 1 - row;
      const l = layers[arrIdx];
      const r = el("div", "layer-row" + (l.id === this.doc.state.activeLayerId ? " active" : ""));
      r.draggable = true;
      r.dataset.testid = `layer-row-${row}`;
      r.dataset.active = l.id === this.doc.state.activeLayerId ? "true" : "false";
      const chk = document.createElement("input");
      chk.type = "checkbox"; chk.checked = l.visible;
      chk.title = "visible"; chk.setAttribute("aria-label", "layer visible");
      chk.dataset.testid = `layer-visible-${row}`;
      chk.addEventListener("change", () => this.safeOp(() => { this.beginEdit(); this.doc.setLayerVisible(l.id, chk.checked); }));
      const nameEl = el("span", "lname", l.name);
      nameEl.title = "double-click to rename";
      nameEl.addEventListener("dblclick", () => {
        const inp = document.createElement("input");
        inp.value = l.name;
        inp.className = "rename-input";
        nameEl.replaceWith(inp);
        let done = false;
        const finish = () => {
          if (done || !inp.value.trim()) return;
          done = true;
          this.safeOp(() => { this.beginEdit(); this.doc.renameLayer(l.id, inp.value); });
        };
        inp.addEventListener("blur", finish);
        inp.addEventListener("keydown", (e) => {
          if (e.key === "Enter") finish();
          e.stopPropagation();
        });
        inp.focus();
      });
      r.addEventListener("selectstart", (e) => e.stopPropagation());
      r.addEventListener("pointerdown", (event) => {
        const target = event.target as HTMLElement;
        if (target.tagName === "INPUT") return;
        this.safeOpNoChange(() => { this.doc.setActiveLayer(l.id); this.renderAll(); });
      });
      const slider = document.createElement("input");
      slider.type = "range"; slider.min = "0"; slider.max = "100"; slider.value = String(Math.round(l.opacity * 100));
      slider.title = "opacity";
      slider.dataset.testid = `layer-opacity-${row}`;
      const val = el("span", "lopacity", `${Math.round(l.opacity * 100)}%`);
      slider.addEventListener("input", () => {
        this.safeOp(() => { this.beginEdit(); this.doc.setLayerOpacity(l.id, Number(slider.value) / 100); });
        val.textContent = `${slider.value}%`;
      });
      r.append(chk, nameEl, slider, val);
      // drag & drop reorder: rows are in display order top->bottom
      r.addEventListener("dragstart", (e) => {
        if (e.dataTransfer) e.dataTransfer.setData("text/plain", String(arrIdx));
      });
      r.addEventListener("dragover", (e) => e.preventDefault());
      r.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer ? e.dataTransfer.getData("text/plain") : "-1");
        if (from !== arrIdx) this.safeOp(() => { this.beginEdit(); this.doc.moveLayer(from, arrIdx); });
      });
      list.appendChild(r);
    }
  }

  safeOpNoChange(fn: () => void): void {
    try { fn(); } catch (e) { this.status(e instanceof Error ? e.message : String(e)); }
  }

  renderStrip(): void {
    const strip = this.els.strip;
    strip.innerHTML = "";
    const st = this.doc.state;
    st.frames.forEach((f, i) => {
      const fig = el("figure", "thumb" + (i === st.activeFrame ? " active-thumb" : ""));
      fig.dataset.testid = `frame-${i}`;
      fig.dataset.active = i === st.activeFrame ? "true" : "false";
      const c = document.createElement("canvas");
      c.width = W; c.height = H;
      c.className = "mini";
      paintComposite(c, compositeWithOnion(st, i, null, 0));
      const cap = el("figcaption", "", `${i + 1}·${f.hold}`);
      fig.append(c, cap);
      fig.draggable = true;
      fig.addEventListener("click", () => {
        this.doc.setActiveFrame(i);
        this.renderAll();
        this.status(`scrubbed to frame ${i + 1}`);
      });
      fig.addEventListener("dragstart", (e) => {
        if (e.dataTransfer) e.dataTransfer.setData("text/plain", String(i));
      });
      fig.addEventListener("dragover", (e) => e.preventDefault());
      fig.addEventListener("drop", (e) => {
        e.preventDefault();
        const from = Number(e.dataTransfer ? e.dataTransfer.getData("text/plain") : "-1");
        if (from !== i) this.safeOp(() => { this.beginEdit(); this.doc.moveFrame(from, i); });
      });
      strip.appendChild(fig);
    });
    const fpsInput = this.els.fps as HTMLInputElement;
    fpsInput.value = String(st.fps);
    const loopChk = this.els.loop as HTMLInputElement;
    loopChk.checked = st.loop;
    const holdInput = this.els.hold as HTMLInputElement;
    holdInput.value = String(st.frames[st.activeFrame].hold);
  }

  renderAll(): void {
    this.renderCanvas();
    this.renderPalette();
    this.renderLayers();
    this.renderStrip();
    this.renderToolbar();
  }
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function btn(label: string, cls: string): HTMLButtonElement {
  const b = document.createElement("button");
  b.textContent = label;
  b.className = "smallbtn tool-btn " + cls;
  return b;
}

window.addEventListener("DOMContentLoaded", () => new App().init());

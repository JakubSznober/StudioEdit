"""Eksport do Adobe After Effects: skrypt ExtendScript (ES3) budujący projekt, oraz uruchomienie AfterFX.exe -r.

Skrypt: foldery (Footage/A-Roll, B-Roll, Audio, Grafika, Odrzucone_Duble...), import plików, kompozycja na każdy
włączony wariant (rozmiar, fps, długość), warstwy ze startTime/inPoint/outPoint, stretch dla prędkości,
klatki kluczowe Scale/Position/Rotation (punch-in, slow zoom, reframe), Opacity dla przejść, warstwy tekstowe dla
TitleClip, markery, warstwy audio (A1..A5 z poziomem), na końcu zapis `.aep` obok skryptu.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

from ..project.model import Timeline, VClip, Variant
from . import (audio_items, base_scale, effective_speed, enabled_variants, fit_mode, kf_value, media_dims, media_of,
               reframe_at, safe_name, title_clips, video_clips)
from .adobe import find_adobe

__all__ = ["export_ae_jsx", "run_ae", "find_adobe", "validate_jsx"]

_JSX = r"""// StudioEdit -> Adobe After Effects (ExtendScript ES3). Wygenerowano automatycznie.
(function () {
    var D = __DATA__;
    var LOG = [];
    function log(s) { LOG.push(s); }
    app.beginUndoGroup("StudioEdit: import montażu");
    var proj = app.project;
    if (!proj) { proj = app.newProject(); }
    var root = proj.items.addFolder(D.name);
    var footage = proj.items.addFolder("Footage");
    footage.parentFolder = root;
    var folders = {};
    function folder(name) {
        if (!folders[name]) {
            var f = proj.items.addFolder(name);
            f.parentFolder = (name === "Kompozycje") ? root : footage;
            folders[name] = f;
        }
        return folders[name];
    }
    folder("Odrzucone_Duble");
    var items = {};
    var i, j, k;
    for (i = 0; i < D.media.length; i++) {
        var m = D.media[i];
        var f = new File(m.path);
        if (!f.exists) { log("Brak pliku: " + m.path); continue; }
        try {
            var io = new ImportOptions(f);
            if (io.canImportAs(ImportAsType.FOOTAGE)) { io.importAs = ImportAsType.FOOTAGE; }
            if (m.still) { io.sequence = false; }
            var it = proj.importFile(io);
            it.parentFolder = folder(m.bin);
            items[m.id] = it;
        } catch (e) { log("Import nieudany: " + m.path + " (" + e.toString() + ")"); }
    }
    function setKeys(prop, keys, isArray) {
        if (!keys || keys.length === 0) { return; }
        if (keys.length === 1) { prop.setValue(keys[0][1]); return; }
        for (k = 0; k < keys.length; k++) { prop.setValueAtTime(keys[k][0], keys[k][1]); }
        for (k = 0; k < keys.length; k++) {
            var ease = keys[k][2];
            var idx = k + 1;
            try {
                if (ease === "hold") {
                    prop.setInterpolationTypeAtKey(idx, KeyframeInterpolationType.HOLD);
                } else if (ease === "ease") {
                    var e1 = new KeyframeEase(0, 33.33);
                    var dims = isArray ? (prop.propertyValueType === PropertyValueType.TwoD_SPATIAL ||
                        prop.propertyValueType === PropertyValueType.ThreeD_SPATIAL ? 1 :
                        (prop.value.length ? prop.value.length : 1)) : 1;
                    var arr = [];
                    for (var q = 0; q < dims; q++) { arr.push(e1); }
                    prop.setTemporalEaseAtKey(idx, arr, arr);
                } else {
                    prop.setInterpolationTypeAtKey(idx, KeyframeInterpolationType.LINEAR);
                }
            } catch (e2) { log("Interpolacja: " + e2.toString()); }
        }
    }
    function tr(ly, name) { return ly.property("ADBE Transform Group").property(name); }
    function placeLayer(comp, L, it) {
        var ly = comp.layers.add(it);
        ly.name = L.name;
        if (L.stretch !== 100) { ly.stretch = L.stretch; }
        ly.startTime = L.startTime;
        ly.inPoint = L.inPoint;
        ly.outPoint = L.outPoint;
        return ly;
    }
    for (i = 0; i < D.comps.length; i++) {
        var C = D.comps[i];
        var comp = proj.items.addComp(C.name, C.w, C.h, 1.0, C.duration, C.fps);
        comp.parentFolder = folder("Kompozycje");
        comp.bgColor = [0, 0, 0];
        for (j = 0; j < C.audio.length; j++) {
            var A = C.audio[j];
            var ait = items[A.media];
            if (!ait) { continue; }
            try {
                var al = placeLayer(comp, A, ait);
                if (al.hasVideo) { al.enabled = false; }
                al.audioEnabled = true;
                if (A.gain !== 0) {
                    al.property("ADBE Audio Group").property("ADBE Audio Levels").setValue([A.gain, A.gain]);
                }
            } catch (e3) { log("Audio " + A.name + ": " + e3.toString()); }
        }
        for (j = 0; j < C.layers.length; j++) {
            var L = C.layers[j];
            var vit = items[L.media];
            if (!vit) { continue; }
            try {
                if (L.blurbg) {
                    var bg = placeLayer(comp, L, vit);
                    bg.name = L.name + " (tło)";
                    bg.audioEnabled = false;
                    tr(bg, "ADBE Scale").setValue([L.cover, L.cover]);
                    var gb = bg.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
                    gb.property(1).setValue(80);
                    try { gb.property(3).setValue(1); } catch (e4) {}
                    setKeys(tr(bg, "ADBE Opacity"), L.opacity, false);
                }
                var ly = placeLayer(comp, L, vit);
                ly.audioEnabled = false;
                setKeys(tr(ly, "ADBE Scale"), L.scale, true);
                setKeys(tr(ly, "ADBE Position"), L.position, true);
                setKeys(tr(ly, "ADBE Rotate Z"), L.rotation, false);
                setKeys(tr(ly, "ADBE Opacity"), L.opacity, false);
                if (L.comment) { ly.comment = L.comment; }
            } catch (e5) { log("Warstwa " + L.name + ": " + e5.toString()); }
        }
        for (j = 0; j < C.texts.length; j++) {
            var T = C.texts[j];
            try {
                var tl = comp.layers.addText(T.text);
                tl.name = "Tytuł: " + T.text.substr(0, 30);
                var tp = tl.property("ADBE Text Properties").property("ADBE Text Document");
                var doc = tp.value;
                doc.fontSize = T.size;
                doc.fillColor = T.color;
                try { doc.font = T.font; } catch (e6) {}
                doc.applyStroke = true;
                doc.strokeColor = [0, 0, 0];
                doc.strokeWidth = Math.max(1, T.size / 18);
                doc.justification = T.left ? ParagraphJustification.LEFT_JUSTIFY : ParagraphJustification.CENTER_JUSTIFY;
                tp.setValue(doc);
                tl.inPoint = T.start;
                tl.outPoint = T.end;
                tr(tl, "ADBE Position").setValue(T.pos);
                setKeys(tr(tl, "ADBE Opacity"), T.opacity, false);
            } catch (e7) { log("Tekst: " + e7.toString()); }
        }
        for (j = 0; j < C.markers.length; j++) {
            try { comp.markerProperty.setValueAtTime(C.markers[j].t, new MarkerValue(C.markers[j].label)); }
            catch (e8) { log("Marker: " + e8.toString()); }
        }
    }
    app.endUndoGroup();
    try { proj.save(new File(D.aep)); } catch (e9) { log("Zapis .aep nieudany: " + e9.toString()); }
    try {
        var lf = new File(D.logfile);
        lf.encoding = "UTF-8";
        lf.open("w");
        lf.write(LOG.length ? LOG.join("\n") : "OK");
        lf.close();
    } catch (e10) {}
})();
"""


def _clip_layer(project, clip: VClip, variant: Variant, track: str, fps: float) -> dict | None:
    m = media_of(project, clip.media_id)
    if m is None:
        return None
    sw, sh = media_dims(m)
    W, H = variant.width, variant.height
    mode = fit_mode(clip, sw, sh, variant, track)
    bs = base_scale(mode, sw, sh, W, H, variant.width)
    sp = effective_speed(clip)
    still = m.kind == "image" or clip.role == "image"
    taus = sorted({0.0, clip.duration, *[k.t for k in clip.transform if 0 <= k.t <= clip.duration]})
    path = clip.reframe.get(variant.name) if mode == "reframe" else None
    if path:
        taus = sorted(set(taus) | {float(p.get("t", 0)) for p in path if 0 <= float(p.get("t", 0)) <= clip.duration})
    eases = {round(k.t, 4): k.ease for k in clip.transform}
    scale, pos, rot = [], [], []
    for t in taus:
        s = bs * kf_value(clip, t, "scale")
        cx, cy = reframe_at(clip, variant.name, t) if path else (0.5, 0.5)
        x = W / 2 + kf_value(clip, t, "x") * W + (0.5 - cx) * sw * s
        y = H / 2 + kf_value(clip, t, "y") * H + (0.5 - cy) * sh * s
        e = eases.get(round(t, 4), "linear" if path else "ease")
        ct = round(clip.start + t, 5)
        scale.append([ct, [round(s * 100, 4), round(s * 100, 4)], e])
        pos.append([ct, [round(x, 3), round(y, 3)], e])
        rot.append([ct, round(kf_value(clip, t, "rot"), 4), e])

    def squash(keys):
        if len({json.dumps(k[1]) for k in keys}) == 1:
            return [keys[0]]
        return keys

    return {"media": m.id, "name": clip.label or m.name,
            "startTime": round(clip.start - (0 if still else clip.src_in / sp), 5),
            "inPoint": round(clip.start, 5), "outPoint": round(clip.end, 5),
            "stretch": 100.0 if still else round(100.0 / sp, 4),
            "scale": squash(scale), "position": squash(pos), "rotation": squash(rot),
            "opacity": [[round(clip.start, 5), round(clip.opacity * 100, 2), "linear"]],
            "blurbg": mode == "blur", "cover": round(max(W / sw, H / sh) * 100, 3),
            "comment": "Speed ramp uproszczony do średniej prędkości" if clip.speed_ramp else ""}


def _apply_transitions(tl: Timeline, layers: dict[str, dict], fps: float) -> None:
    v1 = video_clips(tl, "V1")
    tol = 1.5 / fps + 0.02
    for tr in tl.transitions:
        if tr.kind == "cut" or tr.duration <= 0:
            continue
        A = next((c for c in v1 if abs(c.end - tr.at) <= tol), None)
        B = next((c for c in v1 if abs(c.start - tr.at) <= tol), None)
        h = tr.duration / 2
        a0, a1 = round(tr.at - h, 5), round(tr.at + h, 5)
        if tr.kind in ("dip_black", "dip_white"):
            if A is not None and A.id in layers:
                L = layers[A.id]
                L["opacity"] = [[a0, A.opacity * 100, "linear"], [round(tr.at, 5), 0, "linear"]]
            if B is not None and B.id in layers:
                L = layers[B.id]
                L["opacity"] = [[round(tr.at, 5), 0, "linear"], [a1, B.opacity * 100, "linear"]]
            continue
        # przenikanie: B wchodzi wcześniej (uchwyt) z opacity 0 -> 100, A wydłużone do końca przejścia
        if B is not None and B.id in layers:
            L = layers[B.id]
            if abs(L["startTime"] - L["inPoint"]) < 1e-6:  # grafika/still - można przesunąć start
                L["startTime"] = a0
            L["inPoint"] = max(L["startTime"], a0)  # wideo: nie wcześniej niż początek pliku
            L["opacity"] = [[a0, 0, "linear"], [a1, B.opacity * 100, "linear"]]
        if A is not None and A.id in layers:
            layers[A.id]["outPoint"] = a1


def build_data(project, timeline: Timeline, aep_path: Path, log_path: Path) -> dict:
    tl = timeline.model_copy(deep=True)
    tl.normalize()
    fps = float(tl.fps or 30)
    name = getattr(getattr(project, "meta", None), "name", "") or "StudioEdit"
    media = []
    seen = set()
    for m in project.media():
        if not Path(m.path).exists():
            continue
        media.append({"id": m.id, "path": str(Path(m.path).resolve()).replace("\\", "/"),
                      "bin": m.bin or "A-Roll", "still": m.kind == "image"})
        seen.add(m.id)
    extra = {}
    auds = audio_items(project, tl)
    for it in auds:
        if it.media_id and it.media_id in seen:
            continue
        key = "p:" + str(Path(it.path).resolve()).lower()
        if key not in extra and Path(it.path).exists():
            extra[key] = {"id": f"x{len(extra) + 1}", "path": str(Path(it.path).resolve()).replace("\\", "/"),
                          "bin": "Audio", "still": False}
    media += list(extra.values())
    comps = []
    for v in enabled_variants(tl):
        layers: dict[str, dict] = {}
        order: list[str] = []
        for track in ("V1", "V2", "V3"):
            for c in video_clips(tl, track):
                L = _clip_layer(project, c, v, track, fps)
                if L:
                    layers[c.id] = L
                    order.append(c.id)
        _apply_transitions(tl, layers, fps)
        audio = []
        for it in auds:
            mid = it.media_id if it.media_id in seen else extra.get("p:" + str(Path(it.path).resolve()).lower(),
                                                                    {}).get("id")
            if not mid:
                continue
            audio.append({"media": mid, "name": f"{it.track} {it.label}",
                          "startTime": round(it.start - it.src_in / it.speed, 5), "inPoint": round(it.start, 5),
                          "outPoint": round(it.start + it.duration, 5), "stretch": round(100 / it.speed, 4),
                          "gain": round(it.gain_db, 2)})
        unit = min(v.width, v.height)
        texts = []
        for t in title_clips(tl):
            size = float((t.params or {}).get("size") or unit * (0.09 if t.style == "title" else 0.055))
            pos = t.position or "bottom"
            left = t.style == "lower_third"
            x = v.width * (0.07 if left else 0.5)
            y = v.height * {"top": 0.16, "middle": 0.5, "bottom": 0.82 if v.height > v.width else 0.86}.get(pos, 0.86)
            col = str((t.params or {}).get("color") or "#FFFFFF").lstrip("#")
            try:
                rgb = [int(col[i:i + 2], 16) / 255 for i in (0, 2, 4)]
            except ValueError:
                rgb = [1, 1, 1]
            sub = (t.params or {}).get("subtitle")
            text = t.text + (f"\r{sub}" if sub else "")
            fi = min(0.3, t.duration / 4)
            texts.append({"text": text, "start": round(t.start, 5), "end": round(t.end, 5), "size": round(size, 1),
                          "pos": [round(x, 1), round(y, 1)], "left": left, "color": rgb,
                          "font": (t.params or {}).get("font_ps") or "SegoeUI-Black",
                          "opacity": [[round(t.start, 5), 0, "linear"], [round(t.start + fi, 5), 100, "linear"],
                                      [round(t.end - fi, 5), 100, "linear"], [round(t.end, 5), 0, "linear"]]})
        comps.append({"name": f"{safe_name(name)} – {v.name}", "w": v.width, "h": v.height, "fps": fps,
                      "duration": round(max(1 / fps, tl.duration), 5), "layers": [layers[i] for i in order],
                      "audio": audio, "texts": texts,
                      "markers": [{"t": round(mk.t, 5), "label": mk.label} for mk in tl.markers]})
    return {"name": safe_name(name), "media": media, "comps": comps,
            "aep": str(aep_path.resolve()).replace("\\", "/"), "logfile": str(log_path.resolve()).replace("\\", "/")}


def validate_jsx(path: Path) -> dict:
    """Składnia JSX (parser esprima, jeśli zainstalowany) + zakaz konstrukcji spoza ES3 (let/const/=>/`)."""
    src = Path(path).read_text(encoding="utf-8-sig")
    problems = []
    code = re.sub(r'"(?:\\.|[^"\\])*"', '""', src)
    code = re.sub(r"//[^\n]*", "", code)
    for pat, msg in ((r"\blet\s", "let"), (r"\bconst\s", "const"), (r"=>", "funkcja strzałkowa"), (r"`", "template")):
        if re.search(pat, code):
            problems.append(f"Niedozwolona konstrukcja ES6: {msg}")
    parsed = None
    try:
        import esprima  # type: ignore

        esprima.parseScript(src)
        parsed = True
    except ImportError:
        parsed = None
    except Exception as e:
        parsed = False
        problems.append(f"Błąd składni: {e}")
    return {"ok": not problems, "parsed": parsed, "problems": problems}


def export_ae_jsx(project, timeline: Timeline, out_path: Path) -> Path:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = build_data(project, timeline, out_path.with_suffix(".aep"), out_path.with_suffix(".log.txt"))
    js = _JSX.replace("__DATA__", json.dumps(data, ensure_ascii=True, indent=1))
    # ExtendScript czyta UTF-8 z BOM poprawnie (polskie nazwy w komentarzach/stringach)
    out_path.write_text(js, encoding="utf-8-sig")
    rep = validate_jsx(out_path)
    if not rep["ok"]:
        raise ValueError("Wygenerowany JSX jest niepoprawny: " + "; ".join(rep["problems"]))
    return out_path


def run_ae(jsx_path: Path, timeout: float = 900) -> dict:
    """Uruchamia After Effects ze skryptem (AfterFX.exe -r). Czeka na zapis .aep maks. `timeout` s."""
    jsx_path = Path(jsx_path)
    ad = find_adobe()
    exe = ad.get("aftereffects")
    if not exe:
        return {"ok": False, "message": "After Effects nie jest zainstalowany. Skrypt JSX jest gotowy — można go "
                                        "uruchomić ręcznie w AE (Plik → Skrypty → Uruchom plik skryptu…)."}
    if not jsx_path.exists():
        return {"ok": False, "message": f"Brak skryptu {jsx_path}"}
    aep = jsx_path.with_suffix(".aep")
    before = aep.stat().st_mtime if aep.exists() else 0
    try:
        subprocess.Popen([exe, "-r", str(jsx_path)], creationflags=0x08000000 if hasattr(subprocess, "STARTUPINFO") else 0)
    except OSError as e:
        return {"ok": False, "message": f"Nie udało się uruchomić After Effects: {e}"}
    t0 = time.time()
    while time.time() - t0 < timeout:
        if aep.exists() and aep.stat().st_mtime > before:
            time.sleep(1.0)
            logf = jsx_path.with_suffix(".log.txt")
            msg = logf.read_text(encoding="utf-8", errors="ignore") if logf.exists() else "OK"
            return {"ok": True, "message": f"Projekt After Effects zapisany: {aep.name}" +
                                           ("" if msg.strip() == "OK" else f" (uwagi: {msg[:500]})"),
                    "aep": str(aep)}
        time.sleep(2.0)
    return {"ok": False, "message": f"After Effects nie zapisał projektu w ciągu {int(timeout)} s — sprawdź okno AE "
                                    f"(skrypt: {jsx_path.name})."}

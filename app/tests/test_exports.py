"""Testy eksportu: Premiere XML (FCP7 xmeml v4), After Effects JSX (ES3), EDL CMX3600, OTIO JSON, wykrywanie Adobe."""
from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from studio import paths

paths.setup_env()

from studio.exports import pathurl, rate_info  # noqa: E402
from studio.exports.adobe import find_adobe  # noqa: E402
from studio.exports.aftereffects import export_ae_jsx, validate_jsx  # noqa: E402
from studio.exports.edl import export_edl, export_otio  # noqa: E402
from studio.exports.premiere import export_premiere_xml, validate_xml  # noqa: E402
from studio.project.model import Keyframe, Marker, TitleClip, Timeline, Transition, VClip, Variant  # noqa: E402

TEST = Path(r"C:\StudioEdit\test.MP4")
OUT = paths.CACHE / "dev_render" / "pytest_exports"


def test_helpers():
    assert pathurl(r"C:\Folder z spacją\plik ą.mp4").startswith("file://localhost/C:/Folder%20z%20spacj")
    assert rate_info(29.97) == (30, True)
    assert rate_info(25.0) == (25, False)


@pytest.fixture(scope="module")
def proj():
    from studio.media.ffmpeg import media_info
    from studio.project.manager import ProjectManager

    if not TEST.exists():
        pytest.skip("brak test.MP4")
    root = OUT / "projects"
    shutil.rmtree(root, ignore_errors=True)
    p = ProjectManager(root=root).create("Eksport ąę test", ingest_mode="link")
    m = p.add_media_file(TEST)
    info = media_info(TEST)
    p.update_media(m.id, lambda x: x.info.update(info))
    tl = Timeline(fps=30, variants=[Variant(name="youtube"),
                                    Variant(name="tiktok", aspect="9:16", width=1080, height=1920)])
    tl.tracks["V1"] = [
        VClip(id="a", media_id=m.id, src_in=0, src_out=3, start=0, label="Ujęcie A",
              transform=[Keyframe(t=0, scale=1.0, ease="linear"), Keyframe(t=3, scale=1.08)]),
        VClip(id="b", media_id=m.id, src_in=4, src_out=5, start=3, speed=0.5,
              reframe={"youtube": [{"t": 0, "cx": 0.5, "cy": 0.3}, {"t": 2, "cx": 0.5, "cy": 0.7}]}),
    ]
    tl.tracks["V2"] = [VClip(id="br", media_id=m.id, src_in=10, src_out=11, start=1, role="b_roll", opacity=0.8)]
    tl.tracks["V3"] = [TitleClip(text="Tytuł „test” & <znaki>", start=0.5, duration=2, style="lower_third")]
    tl.transitions = [Transition(at=3.0, kind="dissolve", duration=0.5)]
    tl.markers = [Marker(t=1.0, label="Hook", kind="hook")]
    tl.subtitles.enabled = False
    return p, tl.normalize()


def test_premiere_xml(proj):
    p, tl = proj
    out = export_premiere_xml(p, tl, OUT / "seq.xml")
    rep = validate_xml(out, tl)
    assert rep["ok"], rep["problems"]
    root = ET.parse(out).getroot()
    assert root.tag == "xmeml" and root.get("version") == "4"
    names = [b.findtext("name") for b in root.iter("bin")]
    assert "Odrzucone_Duble" in names and "A-Roll" in names
    seq = root.find(".//sequence")
    assert seq.find("./media/video/format/samplecharacteristics/width").text == "1920"
    assert any(e.findtext("effectid") == "timeremap" for e in root.iter("effect"))
    assert any(e.findtext("name") == "Cross Dissolve" for e in root.iter("effect"))
    assert any(e.findtext("effectid") == "basic" for e in root.iter("effect"))
    assert root.find(".//generatoritem") is not None
    b = [ci for ci in seq.iter("clipitem") if ci.findtext("name") == "b"] or \
        [ci for ci in seq.find("./media/video/track").findall("clipitem")][1:2]
    ci = b[0]
    assert int(ci.findtext("end")) - int(ci.findtext("start")) == 60  # 1 s źródła przy 50% = 2 s
    assert int(ci.findtext("out")) - int(ci.findtext("in")) == 30


def test_ae_jsx(proj):
    p, tl = proj
    out = export_ae_jsx(p, tl, OUT / "proj.jsx")
    rep = validate_jsx(out)
    assert rep["ok"], rep["problems"]
    src = out.read_text(encoding="utf-8-sig")
    assert "addComp" in src and "importFile" in src and "proj.save" in src
    assert "=>" not in src and "let " not in src.replace("\"", "")


def test_edl_and_otio(proj):
    p, tl = proj
    e = export_edl(p, tl, OUT / "seq.edl").read_text(encoding="utf-8")
    assert "TITLE:" in e and "FCM: NON-DROP FRAME" in e
    assert " D    015 " in e  # przenikanie 15 klatek
    assert "M2" in e  # zmiana prędkości
    o = json.loads(export_otio(p, tl, OUT / "seq.otio").read_text(encoding="utf-8"))
    assert o["OTIO_SCHEMA"] == "Timeline.1"
    v1 = o["tracks"]["children"][0]
    assert v1["kind"] == "Video" and any(c["OTIO_SCHEMA"] == "Transition.1" for c in v1["children"])


def test_find_adobe_shape():
    r = find_adobe()
    assert set(r) >= {"premiere", "aftereffects", "versions"}

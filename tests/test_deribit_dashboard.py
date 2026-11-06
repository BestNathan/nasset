"""Offline archive-to-Pages integrity and deployment contracts."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import runpy

import pytest
import yaml

from nasset.deribit_parquet import encode_part, part_path
from nasset.deribit_publish import git_blob_sha

M = runpy.run_path("scripts/build_deribit_dashboard.py")
ROOT = Path(__file__).resolve().parents[1]


class FakeArchive:
    def __init__(self):
        self.objects = {}
        self.entries = {}

    def add(self, path, content):
        sha = git_blob_sha(content)
        self.objects[sha] = content
        self.entries[path] = {"path": path, "type": "blob", "sha": sha, "size": len(content)}
        return sha

    def tree(self):
        return {"archive_commit": "a"*40, "truncated": False, "tree": list(self.entries.values())}

    def blob(self, sha):
        return self.objects[sha]


def write_hour(reader, slot):
    captured = slot + timedelta(minutes=12)
    parts = {
        part_path("index_price",slot): encode_part("index_price", [{
            "base_currency":"BTC","quote_currency":"USD","index_name":"btc_usd","index_price":109000.5
        }], slot=slot, captured_at=captured),
        part_path("volatility_index",slot,underlying="BTC"): encode_part("volatility_index", [{
            "base_currency":"BTC","event_timestamp":int((slot-timedelta(hours=1)).timestamp()*1000),
            "open":40.0,"high":42.0,"low":39.0,"close":41.4,
        }], slot=slot, captured_at=captured),
        part_path("option_chain",slot,underlying="BTC"): encode_part("option_chain", [{
            "instrument_name":"BTC-27NOV26-110000-C","base_currency":"BTC","option_type":"call",
            "expiration_timestamp":int((slot+timedelta(days=21)).timestamp()*1000),
            "strike":110000.0,"bid_price":.02,"ask_price":.03,"mark_iv":45.1,
            "underlying_price":109000.5,"open_interest":4.1,"settlement_currency":"BTC",
        }], slot=slot, captured_at=captured),
        part_path("futures_curve",slot,underlying="BTC"): encode_part("futures_curve", [{
            "instrument_name":"BTC-PERPETUAL","base_currency":"BTC","mark_price":109002.0,
            "bid_price":109001.0,"ask_price":109003.0,"open_interest":9.0,
            "settlement_currency":"BTC",
        }],slot=slot,captured_at=captured),
    }
    hashes={path:reader.add(path,data) for path,data in parts.items()}
    base="deribit/slots/"+slot.strftime("%Y/%m/%d/%H00")
    receipt_path=base+".receipt"
    receipt=b"deribit-slot-v1\n"+json.dumps({
        "version":1,"provider":"deribit","utc_slot":slot.isoformat(),
        "required_paths":list(parts),
    }).encode()
    hashes[receipt_path]=reader.add(receipt_path,receipt)
    manifest=b"deribit-manifest-v1\n"+json.dumps({
        "version":1,"provider":"deribit","receipt_path":receipt_path,
        "receipt_sha":hashes[receipt_path],
        "artifacts":[{"path":p,"sha":sha} for p,sha in sorted(hashes.items())],
    }).encode()
    reader.add(base+".manifest.json",manifest)
    return base


def test_project_recent_hours_from_actual_parquet_and_verified_receipts():
    archive=FakeArchive()
    first=datetime(2026,11,6,11,tzinfo=timezone.utc)
    write_hour(archive,first)
    write_hour(archive,first+timedelta(hours=1))
    result=M["project_archive"](archive,now=first+timedelta(hours=1,minutes=20))
    assert result["archive_commit"]=="a"*40
    assert result["latest_slot"]=="2026-11-06T12:00:00+00:00"
    assert len(result["slots"])==2
    assert result["history"][0]["indexes"][0]["index_name"]=="btc_usd"
    assert result["history"][1]["dvol"][0]["close"]==41.4
    assert result["latest"]["option_chain"][0]["mark_iv"]==45.1
    assert result["latest"]["option_chain"][0]["bid_price"]==.02
    assert "delta" not in result["latest"]["option_chain"][0]
    assert result["latest"]["futures_curve"][0]["mark_price"]==109002.0
    assert result["slots"][-1]["artifact_count"]==5


@pytest.mark.parametrize("change",["missing_manifest","missing_parquet","corrupt_receipt","wrong_hour"])
def test_archive_corruption_fails_instead_of_publishing_fake_data(change):
    archive=FakeArchive()
    stamp=datetime(2026,11,6,12,tzinfo=timezone.utc)
    base=write_hour(archive,stamp)
    if change=="missing_manifest":
        del archive.entries[base+".manifest.json"]
    if change=="missing_parquet":
        del archive.entries[part_path("index_price",stamp)]
    if change=="corrupt_receipt":
        before=archive.objects[archive.entries[base+".receipt"]["sha"]]
        archive.add(base+".receipt",before.replace(b"T12:00",b"T13:00"))
    if change=="wrong_hour":
        p=part_path("index_price",stamp)
        wrong=encode_part("index_price",[{
            "base_currency":"BTC","quote_currency":"USD","index_name":"btc_usd","index_price":2
        }],slot=stamp+timedelta(hours=1),captured_at=stamp+timedelta(hours=1))
        new_sha=archive.add(p,wrong)
        old=archive.objects[archive.entries[base+".manifest.json"]["sha"]]
        manifest=json.loads(old.split(b"\n",1)[1])
        for item in manifest["artifacts"]:
            if item["path"]==p:item["sha"]=new_sha
        archive.add(base+".manifest.json",b"deribit-manifest-v1\n"+json.dumps(manifest).encode())
    with pytest.raises(M["ArchiveIntegrityError"]):
        M["project_archive"](archive)


def test_reader_pins_exact_commit_and_tree():
    class Reader(M["GitHubReader"]):
        def __init__(self):self.calls=[]
        def get(self,path):
            self.calls.append(path)
            if path=="/git/ref/heads/market-data":return {"object":{"sha":"1"*40}}
            if path=="/git/commits/"+"1"*40:return {"tree":{"sha":"2"*40}}
            if path=="/git/trees/"+"2"*40+"?recursive=1":return {"tree":[],"truncated":False}
            raise AssertionError(path)
    reader=Reader()
    assert reader.tree()["archive_commit"]=="1"*40
    assert len(reader.calls)==3
    assert reader.calls[-1]=="/git/trees/"+"2"*40+"?recursive=1"


def test_workflows_are_main_only_and_refresh_pages_after_verified_publication():
    daily=yaml.load((ROOT/".github/workflows/daily.yml").read_text(),Loader=yaml.BaseLoader)
    hourly=yaml.load((ROOT/".github/workflows/deribit-pages.yml").read_text(),Loader=yaml.BaseLoader)
    assert hourly["on"]["workflow_run"]["workflows"]==["Deribit hourly market-data archive"]
    assert hourly["on"]["workflow_run"]["branches"]==["main"]
    assert hourly["permissions"]=={"contents":"read","pages":"write","id-token":"write"}
    assert hourly["concurrency"]["group"]==daily["concurrency"]["group"]=="nasset-pages"
    assert hourly["concurrency"]["cancel-in-progress"]==daily["concurrency"]["cancel-in-progress"]=="false"
    for workflow,job in [(hourly,"deploy"),(daily,"generate")]:
        steps=workflow["jobs"][job]["steps"]
        build=next(s for s in steps if s.get("run")=="python scripts/build_deribit_dashboard.py")
        assert build["env"]["GITHUB_TOKEN"]=="${{ github.token }}"
    assert "github.event.workflow_run.conclusion == 'success'" in hourly["jobs"]["deploy"]["if"]
    assert "head_branch == 'main'" in hourly["jobs"]["deploy"]["if"]
    html=(ROOT/"site/deribit.html").read_text()
    js=(ROOT/"site/deribit.js").read_text()
    assert "deribit.js" in html and "deribit.css" in html
    assert "deribit.html" in (ROOT/"site/index.html").read_text()
    assert "data/deribit-dashboard.json" in js
    assert "data-surface-expiry" in js and "surfaceHeatmap" in js
    assert "riskView" in js and "futuresMatrix" in js
    assert "market()?.greeks_by_instrument" in js
    assert "NOT DEALER NET" in js
    assert "NASSET_AWS" not in js

def test_dashboard_projection_exposes_modelled_analytics_without_native_greek_claims():
    archive=FakeArchive()
    when=datetime(2026,11,6,12,tzinfo=timezone.utc)
    write_hour(archive,when)
    data=M["project_archive"](archive,now=when+timedelta(minutes=20))
    assert data["analytics"]["version"]==1
    assert data["analytics"]["model"].startswith("Black-76")
    btc=data["analytics"]["assets"]["BTC"]
    assert "inverse" in btc["markets"]
    contracts=btc["markets"]["inverse"]
    assert contracts["contracts"]==1
    assert contracts["greeks_by_instrument"]["BTC-27NOV26-110000-C"]["gamma"] > 0
    assert data["history"][0].get("futures") is None
    assert data["latest"]["option_chain"][0]["captured_at"].startswith("2026-11-06T12:12")
    assert "dealer" in data["analytics"]["position_scope"].lower()

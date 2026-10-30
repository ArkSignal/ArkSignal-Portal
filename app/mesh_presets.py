"""Public channel presets: the open channels people actually use on Meshtastic and MeshCore, so an administrator (or a community manager) lists one with a click instead of typing it.

A Meshtastic radio names its first channel after the modem preset (LongFast, MediumFast and so on) and gives it the default key, the same on every radio; that is what makes such a channel open to everyone.
So a preset here is a channel name plus the modem settings that go with it. Only the default-key channels can be listed on a community page (the page builds Meshtastic addresses and QR codes);
the ham-band presets send without any key and MeshCore's own Public channel lives inside its app, so those rows are information only ("addable" is false) and are shown for the radio settings.

The list is reference data taken from the projects' own documents (checked 2026-10-29). The 500 kHz presets are here because of the FCC's 902 to 928 MHz rule (47 CFR 15.247(a)(2), a 500 kHz minimum
bandwidth for digitally modulated systems): Meshtastic's 2.8.1 alpha starts a new node set to the US region from its own menu on LongTurbo, while the US region table and the apps still start on LongFast,
and MeshCore's US list still has 910.525 MHz at 62.5 kHz (a proposal for a 500 kHz US preset is open). The names that matter for the public rules are also kept in the Public Channels setting."""
import re

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,11}$")

# status: "default" (what a radio starts on today), "expected" (announced as the next default), "common", "legacy", "ham", "other"
PRESETS = [
    {"id": "mt-longfast", "system": "Meshtastic", "name": "LongFast", "bw": "250 kHz", "sf": "11", "status": "default", "addable": True,
     "about": "The default for every region today. Best range for its speed; the channel nearly everyone starts on, and the busiest."},
    {"id": "mt-longturbo", "system": "Meshtastic", "name": "LongTurbo", "bw": "500 kHz", "sf": "11", "status": "expected", "addable": True,
     "about": "Expected to become the US default: the 2.8.1 alpha starts a new node set to the US region from its menu on this preset. Its 500 kHz channel is what the FCC's 902 to 928 MHz rule for digitally modulated systems names. Radios on a different preset cannot hear each other."},
    {"id": "mt-mediumturbo", "system": "Meshtastic", "name": "MediumTurbo", "bw": "500 kHz", "sf": "9", "status": "new", "addable": True,
     "about": "New in firmware 2.8: a 500 kHz preset between LongTurbo and ShortTurbo."},
    {"id": "mt-shortturbo", "system": "Meshtastic", "name": "ShortTurbo", "bw": "500 kHz", "sf": "7", "status": "common", "addable": True,
     "about": "The fastest preset, also 500 kHz wide. Short range; for dense places and tests."},
    {"id": "mt-mediumfast", "system": "Meshtastic", "name": "MediumFast", "bw": "250 kHz", "sf": "9", "status": "common", "addable": True,
     "about": "Chosen by some regional groups to take load off LongFast in busy cities."},
    {"id": "mt-mediumslow", "system": "Meshtastic", "name": "MediumSlow", "bw": "250 kHz", "sf": "10", "status": "common", "addable": True,
     "about": "A little more range than MediumFast for a little less speed."},
    {"id": "mt-longmoderate", "system": "Meshtastic", "name": "LongModerate", "bw": "125 kHz", "sf": "11", "status": "common", "addable": True,
     "about": "Long range at a narrower width and a slower rate."},
    {"id": "mt-longslow", "system": "Meshtastic", "name": "LongSlow", "bw": "125 kHz", "sf": "12", "status": "legacy", "addable": True,
     "about": "Older long-range preset; rarely used now."},
    {"id": "mt-shortfast", "system": "Meshtastic", "name": "ShortFast", "bw": "250 kHz", "sf": "7", "status": "common", "addable": True,
     "about": "Short range and fast; for events and close groups."},
    {"id": "mt-shortslow", "system": "Meshtastic", "name": "ShortSlow", "bw": "250 kHz", "sf": "8", "status": "legacy", "addable": True,
     "about": "A little more range than ShortFast."},
    {"id": "mt-vlongslow", "system": "Meshtastic", "name": "VLongSlow", "bw": "62.5 kHz", "sf": "12", "status": "legacy", "addable": True,
     "about": "The slowest, longest-range preset; being retired from the apps."},
    {"id": "mt-tinyfast", "system": "Meshtastic (ham band)", "name": "TinyFast", "bw": "15.6 kHz", "sf": "", "status": "ham", "addable": False,
     "about": "Firmware 2.8.1 alpha: the default on the 2 m amateur band (145.010 MHz). Ham mode sends without encryption, so there is no key to list; the operator needs an amateur license."},
    {"id": "mt-narrowslow", "system": "Meshtastic (ham band)", "name": "NarrowSlow", "bw": "62.5 kHz", "sf": "", "status": "ham", "addable": False,
     "about": "Firmware 2.8.1 alpha: the default on the 1.25 m (223.650 MHz) and 70 cm (433.650 MHz) amateur bands. Sent without encryption; the operator needs an amateur license."},
    {"id": "mc-public", "system": "MeshCore", "name": "Public", "bw": "62.5 kHz", "sf": "7", "status": "default", "addable": False,
     "about": "Built into every MeshCore app and open to everyone (its key is published), so there is nothing to add. The US and Canada setting is 910.525 MHz at 62.5 kHz, SF 7, coding rate 5 (the app's own list can change)."},
    {"id": "mc-usa-500", "system": "MeshCore", "name": "Public", "bw": "500 kHz", "sf": "", "status": "other", "addable": False,
     "about": "A 500 kHz US setting is proposed in the MeshCore project and has not become the default; Philadelphia's mesh moved to a 500 kHz setting in September 2026. Nodes only hear each other when frequency, width and spreading factor all match, so check with the local group before changing."},
]

STATUS = {"default": "Default today", "expected": "Expected US default", "new": "New", "common": "In common use", "legacy": "Older", "ham": "Ham band", "other": "Proposed"}


def by_id(pid):
    return next((p for p in PRESETS if p["id"] == pid), None)


def addable():
    return [p for p in PRESETS if p["addable"]]


def names():
    """The default-key channel names, for the Public Channels setting."""
    return [p["name"] for p in addable()]


def radio(p):
    return (p["bw"] + (", SF " + p["sf"] if p["sf"] else "")).strip()


def label(p):
    return f"{p['name']} ({radio(p)})" if p["system"] == "Meshtastic" else f"{p['system']} {p['name']} ({radio(p)})"


def choices():
    """(id, text) for the Current Public Channel setting."""
    return [(p["id"], label(p)) for p in addable()]


def current(cfg_value):
    """The preset id the site recommends; an id that no longer exists falls back to the first listed."""
    return cfg_value if by_id(cfg_value) and by_id(cfg_value)["addable"] else addable()[0]["id"]

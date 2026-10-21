"""Permissions a role can grant. Super Admin (a flag on a role) implies every permission."""

PERMISSIONS = {
    "manage_users": ("Manage Users", "Create, disable and reset accounts, and assign roles to users."),
    "manage_roles": ("Manage Roles", "Create, edit and delete roles and their permissions."),
    "manage_feeders": ("Manage Feeders", "Edit, reassign, approve and ban any feeder."),
    "manage_settings": ("Manage Settings", "Change SMTP, map defaults, default privacy and feed mode."),
    "manage_weather": ("Manage Weather Stations", "Add, edit, switch off and delete any member's weather station, and add one for a member (without the rest of Manage Feeders)."),
    "manage_sondes": ("Manage Radiosondes", "Assign, approve, release and hide radiosonde receiving stations (without the rest of Manage Feeders)."),
    "manage_map_data": ("Manage Map Data", "Airports, places, hospitals, air ambulances, interesting aircraft, map layers and data updates (without the rest of Manage Settings)."),
    "moderate_repeaters": ("Moderate Repeaters", "Review reports and new repeaters, verify and edit repeater listings, approve sysop claims, add repeaters for members; check, edit and add nets and Winlink gateways."),
    "manage_satimages": ("Manage Satellite Images", "See every satellite receiver and picture, add a receiver for a member, switch one off, hide or delete a picture (without the rest of Manage Settings)."),
    "manage_api_keys": ("Manage API Keys", "See every API key, set its limits, revoke or restore it, and make one for a member (without the rest of Manage Settings)."),
    "moderate_chat": ("Moderate Chat and Messages", "Mute members and delete or dismiss reported chat messages and private messages (a moderator reads only a message that was reported, never the rest of a chat or a mailbox)."),
    "view_history": ("View History", "View the audit log and sign-in history."),
    "view_exact_locations": ("View Exact Locations", "See the exact, private location of every feeder."),
}

# Anyone holding one of these sees the Admin area.
ADMIN_AREA = ("manage_users", "manage_roles", "manage_feeders", "manage_weather", "manage_sondes", "manage_map_data", "manage_api_keys", "manage_satimages", "moderate_repeaters", "moderate_chat", "manage_settings", "view_history")

# A broader permission includes the narrower ones carved out of it, so roles made before the split keep working:
# whoever holds Manage Feeders can still run weather stations and radiosondes, whoever holds Manage Settings can still run the map data pages.
IMPLIED_BY = {"manage_weather": ("manage_feeders",), "manage_sondes": ("manage_feeders",), "manage_map_data": ("manage_settings",), "manage_api_keys": ("manage_settings",), "manage_satimages": ("manage_settings",), "moderate_chat": ("manage_settings",)}

USER_ROLE = "User"
ADMIN_ROLE = "Admin"


# "See <feature>" permissions: one for each feature an administrator can hide (app/features.py). They matter only for a feature whose Feature Access setting is "Only members whose role has the See permission".
from .features import FEATURES as _FEATURES   # noqa: E402

for _key, _label, _sw, _manage in _FEATURES:
    PERMISSIONS["use_" + _key] = ("See " + _label, "See " + _label + " when Settings > Site > Feature Access limits it to roles with this permission. Administrators who run the feature always see it.")

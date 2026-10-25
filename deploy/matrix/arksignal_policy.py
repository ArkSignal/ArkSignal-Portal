"""ArkSignal policy for Synapse (a spam-checker module): who may start a room and who may be invited, as the site's administrators set it (Settings > Matrix Chat). The portal writes /data/policy.json when it syncs;
this module reads it again whenever the file changes, so a change needs no restart.

  create: "everyone" or "moderators"      who may create a room
  invite: "everyone", "friends" or "moderators"   who may invite whom (friends: the two are friends on the site; moderators may always invite)
  contact: {user name: "friends" | "nobody"}   a member's own choice of who may invite them (moderators and the bridge excepted)
  blocks: {user name: [user names they blocked]}   nobody can invite someone who blocked them, or someone they blocked
  moderators: user names that hold Moderate Chat on the site; friends: {user name: [friends' user names]}

The server's own administrator account is always allowed (it makes the standing rooms)."""
import json
import os

from synapse.module_api import NOT_SPAM
from synapse.module_api.errors import Codes


class Policy:
    def __init__(self, config, api):
        self._file = config["policy_file"]
        self._seen = (None, {})
        api.register_spam_checker_callbacks(user_may_create_room=self.may_create, user_may_invite=self.may_invite)

    @staticmethod
    def parse_config(config):
        return config

    def _policy(self):
        try:
            m = os.stat(self._file).st_mtime_ns
            if m != self._seen[0]:
                with open(self._file, encoding="utf-8") as f:
                    self._seen = (m, json.load(f))
        except (OSError, ValueError):
            pass
        return self._seen[1]

    @staticmethod
    def _lp(user_id):
        return user_id[1:].split(":", 1)[0].lower()

    def _privileged(self, lp, p):
        return lp.startswith("arksignal-admin") or lp in [x.lower() for x in p.get("moderators", [])]

    async def may_create(self, user_id):
        p = self._policy()
        if p.get("create", "everyone") == "everyone" or self._privileged(self._lp(user_id), p):
            return NOT_SPAM
        return Codes.FORBIDDEN

    async def may_invite(self, inviter, invitee, room_id):
        p = self._policy()
        a, b = self._lp(inviter), self._lp(invitee)
        blocks = p.get("blocks", {})
        if a in [x.lower() for x in blocks.get(b, [])] or b in [x.lower() for x in blocks.get(a, [])]:
            return Codes.FORBIDDEN   # one of them blocked the other, even a moderator's invitation does not get through
        if self._privileged(a, p) or a.startswith("arksignal-bridge"):
            return NOT_SPAM
        friends = [x.lower() for x in p.get("friends", {}).get(a, [])]
        want = p.get("contact", {}).get(b)   # the invited member's own choice: friends only, or nobody new
        if want in ("friends", "nobody") and b not in friends:
            return Codes.FORBIDDEN
        mode = p.get("invite", "everyone")
        if mode == "everyone" or (mode == "friends" and b in friends):
            return NOT_SPAM
        return Codes.FORBIDDEN

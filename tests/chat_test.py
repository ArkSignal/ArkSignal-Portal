"""Chat as live conversations: direct and group chats between signed-in members, live polling (new messages, typing, seen, online), unread counts, the rules (length, speed, words, new accounts, mutes, blocks), group
controls, reports and moderation, the move from the old private threads, and Feature Access. Runs against the test portal."""
import re, subprocess, requests
exec(open("roles_test.py", encoding="utf-8").read().split("ok_all=True")[0])
ok_all = True
def check(n, c, x=""):
    global ok_all
    print(("PASS " if c else "FAIL ") + n, "" if c else x); ok_all &= bool(c)
def psql(sql):
    return subprocess.run(["ssh", "-q", "nastemp", f"echo $TEST_SUDO_PASSWORD | sudo -S -p '' /usr/local/bin/docker exec adsb-portal-db psql -U portal -d portal -tA -c \"{sql}\""], capture_output=True, text=True).stdout.strip()
def setting(k, v):
    psql(f"INSERT INTO settings (key, value) VALUES ('{k}', '{v}') ON CONFLICT (key) DO UPDATE SET value = '{v}'")
def unset(*ks):
    for k in ks:
        psql(f"DELETE FROM settings WHERE key = '{k}'")
KEYS = ("chat_rate_per_minute", "chat_blocked_words", "chat_new_member_days", "chat_max_length", "chat_group_max", "chat_new_per_day", "access_chat", "chat_enabled", "chat_typing_seconds")
unset(*KEYS)
def wipe():
    for t in ("conv_reports", "conv_messages", "conv_members", "convs", "chat_mutes", "friendships", "pm_blocks", "pm_reports", "pm_messages", "pm_threads"):
        psql(f"DELETE FROM {t}")
    psql("UPDATE users SET contact_policy = 'anyone', pm_open = TRUE, show_online = TRUE")
wipe()
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); third = sess("claimer2", "claimpassword1"); anon = requests.Session()
def csrf(s):
    return re.search(r'data-csrf="([^"]+)"', s.get(B + "/chat/").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)), headers={"X-Requested-With": "XMLHttpRequest"})
def poll(s, cid, after=0):
    return s.get(B + f"/chat/{cid}/poll", params={"after": after}).json()
def uid(name):
    return psql(f"select id from users where username = '{name}'")

# ---- the page and starting chats
check("chat needs a sign-in", anon.get(B + "/chat/", allow_redirects=False).status_code in (302, 401, 403))
pg = me.get(B + "/chat/")
check("a member sees the chat page with the list, the start form and the script (no rooms any more)", pg.status_code == 200 and 'id="chatapp"' in pg.text and "Start a Chat" in pg.text and "/static/chatlive.js" in pg.text and "Rooms" not in pg.text)
check("the script puts messages on the page as text, never HTML", "textContent" in me.get(B + "/static/chatlive.js").text and "innerHTML" not in me.get(B + "/static/chatlive.js").text.replace('listEl.innerHTML = ""', ""))
check("a visitor does not see the Community menu, a member does", 'href="/chat/"' not in anon.get(B + "/").text and 'href="/chat/"' in me.get(B + "/").text and 'href="/my/messages"' in me.get(B + "/").text)
check("a name that does not exist, yourself and nobody chosen are refused plainly", "No member" in post(me, "/chat/new", to="nobodyatall").json()["message"] and "yourself" in post(me, "/chat/new", to="plain1").json()["message"] and "Choose who" in post(me, "/chat/new", to="").json()["message"])
r = post(me, "/chat/new", to="claimer1", body="Hello <b>claimer</b>").json()
check("a member starts a direct chat with a first message", r["ok"] and re.match(r"^/chat/\d+$", r["url"]), r)
cid = r["url"].split("/")[-1]
check("starting it again opens the same chat", post(me, "/chat/new", to="claimer1").json()["url"] == f"/chat/{cid}" and psql("select count(*) from convs") == "1")
check("the other member's list has it with an unread count and their menu badge has the number", other.get(B + "/chat/list.json").json()["chats"][0]["unread"] == 1 and other.get(B + "/chat/unread.json").json()["chat"] == 1 and me.get(B + "/chat/unread.json").json()["chat"] == 0)
lst = other.get(B + "/chat/list.json").json()["chats"][0]
check("the list names the chat for each person, with the last words", lst["title"] == "plain1" and lst["last"] == "Hello <b>claimer</b>" and lst["kind"] == "direct" and me.get(B + "/chat/list.json").json()["chats"][0]["title"] == "Claimer")
pgc = other.get(B + f"/chat/{cid}")
check("the chat page has the message box and the conversation id", pgc.status_code == 200 and f'data-open="{cid}"' in pgc.text and 'id="ch-form"' in pgc.text and "Block plain1" in pgc.text)
p = poll(other, cid)
check("polling gives the message as typed (text), with who and whether it is mine", len(p["messages"]) == 1 and p["messages"][0]["body"] == "Hello <b>claimer</b>" and p["messages"][0]["name"] == "plain1" and not p["messages"][0]["mine"] and p["messages"][0]["report_url"] and p["messages"][0]["del_url"] == "", p)
check("opening it marked it read: the unread count is gone", other.get(B + "/chat/unread.json").json()["chat"] == 0)
mid = p["messages"][0]["id"]
check("polling with the last id returns only what is new", poll(other, cid, mid)["messages"] == [])
check("the sender sees that it was seen", [o for o in poll(me, cid)["others"] if o["name"] == "Claimer"][0]["read"] >= mid)
r = post(other, f"/chat/{cid}/send", body="Hi back")
check("the other member replies and the first sees it at once", r.json()["ok"] and [m["body"] for m in poll(me, cid, mid)["messages"]] == ["Hi back"])
check("a third member cannot read, poll, write in or report in someone else's chat", third.get(B + f"/chat/{cid}").status_code == 404 and third.get(B + f"/chat/{cid}/poll").status_code == 404 and post(third, f"/chat/{cid}/send", body="x").status_code == 404
      and post(third, f"/chat/message/{mid}/report").status_code == 404 and post(adm, f"/chat/{cid}/send", body="x").status_code == 404 and adm.get(B + f"/chat/{cid}").status_code == 404)
# typing
setting("chat_typing_seconds", "6")
post(other, f"/chat/{cid}/typing")
check("typing is shown to the others for a few seconds and not to the typist", poll(me, cid)["typing"] == ["Claimer"] and poll(other, cid)["typing"] == [])
psql("UPDATE conv_members SET typing_at = now() - interval '1 minute'")
check("the typing notice fades", poll(me, cid)["typing"] == [])
check("sending clears the typing notice", (post(other, f"/chat/{cid}/typing"), post(other, f"/chat/{cid}/send", body="done"), poll(me, cid)["typing"] == [])[2])
# online
check("a chat partner who just polled shows online", [o for o in poll(me, cid)["others"] if o["name"] == "Claimer"][0]["online"] is True)
# delete, report
mine_ = [m for m in poll(me, cid)["messages"] if m["mine"]][0]
check("the writer can delete their own message for everyone and the other cannot", post(other, f"/chat/message/{mine_['id']}/delete").status_code == 403 and post(me, f"/chat/message/{mine_['id']}/delete").json()["ok"] and mine_["id"] in poll(other, cid, mine_["id"] - 1)["gone"])
# rules
check("empty and too long messages are refused with plain messages", post(me, f"/chat/{cid}/send", body="  ").json()["message"] == "Write something first." and "500 characters" in post(me, f"/chat/{cid}/send", body="x" * 501).json()["message"])
setting("chat_blocked_words", "badword, worse")
check("a word on the list is refused (whole word, any capitals)", "word this chat does not allow" in post(me, f"/chat/{cid}/send", body="that is a BadWord indeed").json()["message"] and post(me, f"/chat/{cid}/send", body="badwords are fine as part of a longer word").json()["ok"])
unset("chat_blocked_words")
setting("chat_rate_per_minute", "3")
rs = [post(me, f"/chat/{cid}/send", body=f"fast {i}") for i in range(4)]
check("sending too fast is refused", rs[-1].status_code == 400 and "too fast" in rs[-1].json()["message"], [x.status_code for x in rs])
unset("chat_rate_per_minute")
setting("chat_new_member_days", "3650")
check("a brand new account cannot write until the days have passed, but a moderator can", "after 3650 days" in post(me, f"/chat/{cid}/send", body="too new").json()["message"] and post(adm, "/chat/new", to="plain1", body="moderator speaks").json()["ok"])
unset("chat_new_member_days")

# ---- groups
r = post(me, "/chat/new", to="claimer1,claimer2", name="Net Control").json()
gid = r["url"].split("/")[-1]
check("two or more people make a group with a name", r["ok"] and psql(f"select kind from convs where id = {gid}") == "group" and psql(f"select count(*) from conv_members where conv_id = {gid}") == "3", r)
check("the group is in each member's list with its name", all(any(c["title"] == "Net Control" for c in s.get(B + "/chat/list.json").json()["chats"]) for s in (me, other, third)))
post(other, f"/chat/{gid}/send", body="Net at 8")
p3 = poll(third, gid)
check("everyone in the group sees what is said, with who said it", [m["body"] for m in p3["messages"]] == ["Net at 8"] and p3["messages"][0]["name"] == "Claimer" and p3["people"] == 3)
poll(me, gid)
check("seen is per person in a group: only some have read the newest message", [o["read"] for o in poll(me, gid)["others"] if o["name"] == "claimer2"][0] >= p3["messages"][0]["id"] and [o["read"] for o in poll(other, gid)["others"] if o["name"] == "plain1"][0] >= p3["messages"][0]["id"])
check("only the maker renames, adds and removes", post(other, f"/chat/{gid}/rename", name="Mine").status_code == 403 and post(other, f"/chat/{gid}/add", to="marty").status_code == 403 and post(other, f"/chat/{gid}/remove", to="claimer2").status_code == 403)
check("the maker renames the group", post(me, f"/chat/{gid}/rename", name="Tuesday Net").json()["ok"] and psql(f"select name from convs where id = {gid}") == "Tuesday Net")
setting("chat_group_max", "3")
check("the size limit stops adding more", "3 people at most" in post(me, f"/chat/{gid}/add", to="marty").json()["message"] and "3 people at most" in post(me, "/chat/new", to="claimer1,claimer2,marty").json()["message"])
unset("chat_group_max")
check("the maker adds a member, who starts with nothing unread and can then read from now on", post(me, f"/chat/{gid}/add", to="marty").json()["ok"] and adm.get(B + f"/chat/{gid}").status_code == 200 and poll(adm, gid)["people"] == 4)
check("adding someone already in it is refused", "already" in post(me, f"/chat/{gid}/add", to="marty").json()["message"])
check("the maker removes a member and they lose access", post(me, f"/chat/{gid}/remove", to="marty").json()["ok"] and adm.get(B + f"/chat/{gid}").status_code == 404)
check("a member leaves a group; the next member takes over as its maker", post(me, f"/chat/{gid}/leave").json()["ok"] and me.get(B + f"/chat/{gid}").status_code == 404 and psql(f"select created_by from convs where id = {gid}") == uid("claimer1"))
check("a direct chat cannot be 'left', only closed, and closing hides it until someone writes", post(me, f"/chat/{cid}/leave").status_code == 404 and post(me, f"/chat/{cid}/close").json()["ok"] and not [c for c in me.get(B + "/chat/list.json").json()["chats"] if c["id"] == int(cid)]
      and post(other, f"/chat/{cid}/send", body="you there?").json()["ok"] and [c for c in me.get(B + "/chat/list.json").json()["chats"] if c["id"] == int(cid)])
setting("chat_new_per_day", "2")
check("the new-chats-per-day limit stops a member starting more", "lot of chats today" in post(me, "/chat/new", to="claimer2,marty", name="Another").json().get("message", "") or "lot of chats today" in post(me, "/chat/new", to="claimer2").json().get("message", ""))
unset("chat_new_per_day")

# ---- reports, mutes, blocks
rep_mid = [m for m in poll(me, cid)["messages"] if not m["mine"]][-1]["id"]
r = post(me, f"/chat/message/{rep_mid}/report", note="not nice")
check("a member reports a message", r.json()["ok"] and psql("select count(*) from conv_reports") == "1" and "only that message" in r.json()["message"])
check("reporting twice says so and makes no second report", "already" in post(me, f"/chat/message/{rep_mid}/report").json()["message"] and psql("select count(*) from conv_reports") == "1")
ap = adm.get(B + "/admin/chat").text
check("the moderators' page shows only the reported message, its author, the reporter and the note", all(x in ap for x in ("Reports Waiting (1)", "not nice", "reported by plain1", "claimer1", "Delete the Message", "you there?")) and "Hi back" not in ap and "Net at 8" not in ap)
repid = psql("select id from conv_reports limit 1")
r = post(adm, f"/admin/chat/reports/chat/{repid}/delete")
check("deleting from the report removes the message from the chat and closes the report", r.json()["ok"] and rep_mid in poll(me, cid, rep_mid - 1)["gone"] and psql("select count(*) from conv_reports where handled_at is null") == "0")
r = post(adm, "/admin/chat/mute", username="plain1", scope="chat", hours="2", reason="test")
check("a moderator mutes a member from chat for some hours", r.json()["ok"] and "muted in chat" in post(me, f"/chat/{cid}/send", body="hello?").json()["message"].lower() and "You are muted" in me.get(B + "/chat/").text)
check("a muted member cannot start a chat but can still read", not post(me, "/chat/new", to="claimer2").json()["ok"] and poll(me, cid)["messages"] is not None)
r = post(adm, "/admin/chat/unmute", user_id=uid("plain1"), scope="chat")
check("unmuting lets them write again", r.json()["ok"] and post(me, f"/chat/{cid}/send", body="back again").json()["ok"])
post(other, "/my/friends/plain1/block")
check("when a member blocks another, neither can write in their direct chat any more", "cannot write" in post(me, f"/chat/{cid}/send", body="hi?").json()["message"] and "cannot write" in post(other, f"/chat/{cid}/send", body="me too").json()["message"])
post(other, "/my/friends/plain1/unblock")

# ---- the move from the old threads
wipe()
a, b = sorted([int(uid("plain1")), int(uid("claimer1"))])
psql(f"INSERT INTO pm_threads (user_a, user_b) VALUES ({a}, {b})")
tid = psql("select id from pm_threads limit 1")
for who, body, read in ((a, "old one", "now()"), (b, "old two", "now()"), (a, "old unread", "NULL")):
    psql(f"INSERT INTO pm_messages (thread_id, sender_id, body, read_at) VALUES ({tid}, {who}, '{body}', {read})")
psql(f"INSERT INTO pm_messages (thread_id, sender_id, body, hidden_a) VALUES ({tid}, {b}, 'old hidden', TRUE)")
psql("UPDATE users SET pm_open = FALSE WHERE username = 'claimer1'")
psql("DELETE FROM settings WHERE key = 'chat_v2_migrated'")
lst = me.get(B + "/chat/list.json").json()["chats"]
check("an old private thread becomes a direct chat with its messages (not the one a person had deleted)", len(lst) == 1 and lst[0]["title"] == "Claimer" and lst[0]["last"] == "old unread" and psql("select count(*) from conv_messages") == "3", lst)
check("who had read what is kept: the unread old message is still unread for the one it was sent to", [c["unread"] for c in (a == int(uid("plain1")) and other or me).get(B + "/chat/list.json").json()["chats"]][0] == 1)
check("a member who had turned off new conversations is now friends-only", psql("select contact_policy from users where username = 'claimer1'") == "friends")
check("running it again changes nothing", me.get(B + "/chat/list.json").status_code == 200 and psql("select count(*) from conv_messages") == "3" and psql("select count(*) from convs") == "1")

# ---- feature access and the shared pages
setting("access_chat", "roles")
check("with Chat limited to roles a member without the permission sees none of it", me.get(B + "/chat/").status_code == 404 and me.get(B + "/my/messages").status_code == 200)
unset("access_chat")
setting("chat_enabled", "0")
check("with Chat off the pages are gone", me.get(B + "/chat/").status_code == 404 and 'href="/chat/"' not in me.get(B + "/").text)
unset("chat_enabled")
check("the admin menu, the roles page and the settings have it", 'href="/admin/chat"' in adm.get(B + "/admin/").text and "Moderate Chat and Messages" in adm.get(B + "/admin/roles").text
      and all(x in adm.get(B + "/admin/settings").text for x in ("Chat: Longest Message", "Chat: Most People in a Group Chat", "Chat: New Chats a Member Can Start per Day", "Chat: Seconds a Member Counts as Online", "Chat: Seconds the Typing Notice Lasts", "Who Can See Chat")))
au = adm.get(B + "/admin/audit").text
check("chat structure and moderation are in the audit log, never the words", all(x in au for x in ("chat.start", "chat.group_add", "chat.group_remove", "chat.group_leave", "chat.group_rename", "chat.mute", "chat.unmute", "chat.report_delete")) and "Net at 8" not in au and "not nice" not in au.replace("reported message", ""))
check("the overview shows chat counts, and the restore area lists the new tables", "Chat messages, last 24 hours" in adm.get(B + "/admin/").text and all(x in open("../app/restorepart.py", encoding="utf-8").read() for x in ("conv_messages", "friendships", "mail_messages")))
wipe()
unset(*KEYS)
print("ALL PASS" if ok_all else "SOME FAILED")

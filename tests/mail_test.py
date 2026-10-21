"""Messages as email-style mail: compose with recipients and a subject, drafts, inbox, sent, archive, trash, read and unread, threads with Reply and Reply to All, search, the rules (length, recipients, rate, blocks, the
contact rule, mutes), reports and moderators who see only a reported message, and Feature Access. Runs against the test portal."""
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
KEYS = ("pm_min_days", "pm_rate_per_hour", "pm_max_length", "mail_max_recipients", "mail_max_subject", "pm_enabled", "access_messages", "mail_page")
unset(*KEYS)
def wipe():
    for t in ("mail_reports", "mail_recipients", "mail_messages", "chat_mutes", "friendships", "pm_blocks"):
        psql(f"DELETE FROM {t}")
    psql("UPDATE users SET contact_policy = 'anyone', pm_open = TRUE")
wipe()
adm = sess("marty", PW); me = sess("plain1", "plainpassword1"); other = sess("claimer1", "claimpassword1"); third = sess("claimer2", "claimpassword1"); anon = requests.Session()
def csrf(s):
    return re.search(r'var csrf = "([^"]+)"', s.get(B + "/my/messages").text).group(1)
def post(s, path, **data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)), headers={"X-Requested-With": "XMLHttpRequest"})
def postm(s, path, data):
    return s.post(B + path, data=dict(data, csrf_token=csrf(s)))

check("messages need a sign-in", anon.get(B + "/my/messages", allow_redirects=False).status_code in (302, 401, 403))
pg = me.get(B + "/my/messages").text
check("the inbox has the folders, a search box and Write a Message", all(x in pg for x in ("Inbox", "Sent", "Drafts", "Archive", "Trash", "Write a Message", 'type="search"')) and "Your inbox is empty." in pg)
cp = me.get(B + "/my/messages/compose")
check("the compose page has To, Subject, Message, Send and Save as Draft", cp.status_code == 200 and all(x in cp.text for x in ('name="to"', 'name="subject"', 'name="body"', "Send", "Save as Draft")))
check("nobody chosen, a name that does not exist, yourself and an empty message are refused plainly", "Choose who" in post(me, "/my/messages/send", to="", body="x").json()["message"] and "No member" in post(me, "/my/messages/send", to="nobodyatall", subject="s", body="x").json()["message"]
      and "yourself" in post(me, "/my/messages/send", to="plain1", subject="s", body="x").json()["message"] and "Write something" in post(me, "/my/messages/send", to="claimer1", subject="s", body=" ").json()["message"])
r = post(me, "/my/messages/send", to="claimer1, claimer2", subject="Meet on the net?", body="Line one\nLine two <b>bold</b>").json()
check("a message goes to two recipients", r["ok"] and psql("select count(*) from mail_recipients") == "2" and psql("select sender_folder from mail_messages") == "sent", r)
mid = psql("select id from mail_messages")
check("it is in the sender's Sent folder with the recipients named, and not in their Inbox", "Meet on the net?" in me.get(B + "/my/messages?folder=sent").text and "claimer1, claimer2" in me.get(B + "/my/messages?folder=sent").text and "Your inbox is empty." in me.get(B + "/my/messages").text)
ib = other.get(B + "/my/messages").text
check("each recipient sees it in the Inbox as unread with a count in the menu", "Meet on the net?" in ib and "ml-row unread" in ib and other.get(B + "/chat/unread.json").json()["mail"] == 1 and 'class="msgbadge"' in ib)
v = other.get(B + f"/my/messages/{mid}")
check("opening it shows the text as typed (not HTML), marks it read, and offers Reply, Reply to All, Archive, Delete, Report and Block", v.status_code == 200 and "Line two &lt;b&gt;bold&lt;/b&gt;" in v.text and all(x in v.text for x in ("Reply", "Reply to All", "Archive", "Delete", "Report", "Block plain1"))
      and psql("select count(*) from mail_recipients where read_at is null") == "1" and other.get(B + "/chat/unread.json").json()["mail"] == 0)
check("a person who was not a recipient cannot read it, and neither can an administrator", third.get(B + f"/my/messages/{mid}").status_code == 200 and adm.get(B + f"/my/messages/{mid}").status_code == 404 and anon.get(B + f"/my/messages/{mid}", allow_redirects=False).status_code in (302, 401, 403))
psql("DELETE FROM mail_recipients WHERE user_id = (select id from users where username = 'claimer2')")
check("(after taking one recipient away) they cannot read it either", third.get(B + f"/my/messages/{mid}").status_code == 404)
psql(f"INSERT INTO mail_recipients (message_id, user_id) SELECT {mid}, id FROM users WHERE username = 'claimer2'")

# reply and reply to all
rc = other.get(B + f"/my/messages/compose?reply={mid}&all=1").text
check("Reply to All fills in the sender and the other recipients and puts Re: on the subject; Reply fills in only the sender", 'value="plain1, claimer2"' in rc and 'value="Re: Meet on the net?"' in rc and 'value="plain1"' in other.get(B + f"/my/messages/compose?reply={mid}").text)
r = post(other, "/my/messages/send", to="plain1", subject="Re: Meet on the net?", body="Yes, 8 pm.", parent=mid).json()
rid = psql("select max(id) from mail_messages")
check("a reply is sent and joins the thread", r["ok"] and psql(f"select thread_id from mail_messages where id = {rid}") == mid)
vv = me.get(B + f"/my/messages/{rid}").text
check("the original sender sees the whole thread, oldest first, in one page", "Yes, 8 pm." in vv and "Line one" in vv and vv.index("Line one") < vv.index("Yes, 8 pm."))
check("the third person does not see the reply (it was not sent to them)", "Yes, 8 pm." not in third.get(B + f"/my/messages/{mid}").text)

# folders
r = post(other, "/my/messages/bulk", act="archive", ids=mid).json()
check("a message can be archived and is then in the Archive and out of the Inbox", r["ok"] and "Meet on the net?" in other.get(B + "/my/messages?folder=archive").text and "Meet on the net?" not in other.get(B + "/my/messages").text)
post(other, "/my/messages/bulk", act="inbox", ids=mid)
check("and moved back", "Meet on the net?" in other.get(B + "/my/messages").text)
post(other, "/my/messages/bulk", act="unread", ids=mid)
check("it can be marked unread again", other.get(B + "/chat/unread.json").json()["mail"] == 1)
post(other, "/my/messages/bulk", act="read", ids=mid)
check("and read", other.get(B + "/chat/unread.json").json()["mail"] == 0)
post(other, "/my/messages/bulk", act="trash", ids=mid)
check("delete moves it to the Trash (the sender and the other recipient still have theirs)", "Meet on the net?" in other.get(B + "/my/messages?folder=trash").text and "Meet on the net?" in me.get(B + "/my/messages?folder=sent").text and "Meet on the net?" in third.get(B + "/my/messages").text)
post(other, "/my/messages/bulk", act="restore", ids=mid)
check("restore brings it back to the Inbox", "Meet on the net?" in other.get(B + "/my/messages").text)
post(other, "/my/messages/bulk", act="trash", ids=mid)
post(other, "/my/messages/bulk", act="delete", ids=mid)
check("delete from the Trash removes only their own copy for good", psql(f"select count(*) from mail_recipients where message_id = {mid}") == "1" and "Meet on the net?" in me.get(B + "/my/messages?folder=sent").text)
check("delete outside the Trash does nothing (it must go to the Trash first)", post(third, "/my/messages/bulk", act="delete", ids=mid).json()["count"] == 0 and psql(f"select count(*) from mail_recipients where message_id = {mid}") == "1")
post(me, "/my/messages/bulk", act="trash", ids=mid)
check("the sender can trash their sent copy; the recipient keeps theirs", "Meet on the net?" in me.get(B + "/my/messages?folder=trash").text and "Meet on the net?" in third.get(B + "/my/messages").text)
r = post(me, "/my/messages/empty-trash").json()
check("Empty the Trash removes the sender's copy for good", r["ok"] and "Meet on the net?" not in me.get(B + "/my/messages?folder=trash").text and psql(f"select sender_folder from mail_messages where id = {mid}") == "gone")
check("the last copy deleted removes the message itself", (post(third, "/my/messages/bulk", act="trash", ids=mid), post(third, "/my/messages/bulk", act="delete", ids=mid), psql(f"select count(*) from mail_messages where id = {mid}") == "0")[2])

# drafts
r = post(me, "/my/messages/draft", to="claimer1", subject="Draft subject", body="half written").json()
did = r["id"]
check("a draft is saved and listed in Drafts", r["ok"] and "Draft subject" in me.get(B + "/my/messages?folder=drafts").text and psql(f"select sender_folder from mail_messages where id = {did}") == "drafts")
check("opening a draft goes to the compose page with its words", 'value="Draft subject"' in me.get(B + f"/my/messages/{did}").text and "half written" in me.get(B + f"/my/messages/compose?draft={did}").text)
check("a draft is not visible to the person it is for", other.get(B + f"/my/messages/{did}").status_code == 404 and "Draft subject" not in other.get(B + "/my/messages").text)
r = post(me, "/my/messages/draft", to="claimer1", subject="Draft subject", body="finished", draft=did).json()
check("saving again updates the same draft", r["id"] == did and psql("select count(*) from mail_messages where sender_folder = 'drafts'") == "1")
r = post(me, "/my/messages/send", to="claimer1", subject="Draft subject", body="finished", draft=did).json()
check("sending a draft turns it into a sent message to the person", r["ok"] and psql(f"select sender_folder from mail_messages where id = {did}") == "sent" and "finished" in other.get(B + f"/my/messages/{did}").text and psql("select count(*) from mail_messages where sender_folder = 'drafts'") == "0")
r = post(me, "/my/messages/draft", to="claimer2", subject="Junk draft", body="x").json()
post(me, "/my/messages/bulk", act="delete", ids=r["id"])
check("a draft can be deleted for good", "Junk draft" not in me.get(B + "/my/messages?folder=drafts").text)

# search and paging
check("search finds a message by subject, body or sender, only in that folder", "Draft subject" in other.get(B + "/my/messages?q=finished").text and "Draft subject" in other.get(B + "/my/messages?q=plain1").text and "Nothing in this folder matches" in other.get(B + "/my/messages?q=zebra").text)
setting("mail_page", "5")
for i in range(7):
    post(me, "/my/messages/send", to="claimer1", subject=f"Bulk {i}", body="b")
check("a folder pages at the setting", "Page 1 of 2" in other.get(B + "/my/messages").text and "Older" in other.get(B + "/my/messages").text and "Bulk 0" in other.get(B + "/my/messages?page=2").text)
unset("mail_page")

# the rules
setting("mail_max_recipients", "1")
check("the recipient limit is enforced", "1 people at most" in post(me, "/my/messages/send", to="claimer1, claimer2", subject="s", body="x").json()["message"])
unset("mail_max_recipients")
setting("pm_max_length", "50")
check("the length limit is enforced", "50 characters" in post(me, "/my/messages/send", to="claimer1", subject="s", body="x" * 51).json()["message"])
unset("pm_max_length")
setting("mail_max_subject", "20")
check("the subject limit is enforced", "20 characters" in post(me, "/my/messages/send", to="claimer1", subject="s" * 21, body="x").json()["message"])
unset("mail_max_subject")
setting("pm_min_days", "3650")
check("a brand new account cannot send", "after 3650 days" in post(me, "/my/messages/send", to="claimer1", subject="s", body="x").json()["message"])
unset("pm_min_days")
setting("pm_rate_per_hour", "1")
check("the per-hour limit stops more", "a lot of messages" in post(me, "/my/messages/send", to="claimer1", subject="s", body="x").json()["message"])
unset("pm_rate_per_hour")
post(other, "/my/friends/prefs", contact_policy="friends")
check("a friends-only member cannot be written to by a stranger", "only hears from friends" in post(me, "/my/messages/send", to="claimer1", subject="s", body="x").json()["message"])
post(other, "/my/friends/prefs", contact_policy="anyone")
post(other, "/my/friends/plain1/block")
before = psql("select count(*) from mail_recipients")
r = post(me, "/my/messages/send", to="claimer1", subject="Blocked mail", body="x").json()
check("a blocked sender sees it as sent and it is quietly not delivered", r["ok"] and psql("select count(*) from mail_recipients") == before and "Blocked mail" in me.get(B + "/my/messages?folder=sent").text and "Blocked mail" not in other.get(B + "/my/messages").text)
post(other, "/my/friends/plain1/unblock")
r = post(adm, "/admin/chat/mute", username="plain1", scope="pm", reason="test")
check("a moderator can mute a member from messages", r.json()["ok"] and "muted from private messages" in post(me, "/my/messages/send", to="claimer1", subject="s", body="x").json()["message"].lower())
post(adm, "/admin/chat/unmute", user_id=psql("select id from users where username = 'plain1'"), scope="pm")

# reports
vid = psql("select id from mail_messages where subject = 'Draft subject' and sender_folder = 'sent'")
r = post(other, f"/my/messages/{vid}/report", note="spammy").json()
check("a recipient reports a message, and only that message reaches the moderators", r["ok"] and "only that message" in r["message"] and all(x in adm.get(B + "/admin/chat").text for x in ("Draft subject", "finished", "spammy", "reported by claimer1")) and "Bulk 3" not in adm.get(B + "/admin/chat").text)
check("a sender cannot report their own message, nor can a stranger", post(me, f"/my/messages/{vid}/report").status_code == 404 and post(third, f"/my/messages/{vid}/report").status_code == 404)
check("reporting twice says so", "already" in post(other, f"/my/messages/{vid}/report").json()["message"])
rep = psql("select id from mail_reports limit 1")
check("a moderator deletes the reported message and the report closes", post(adm, f"/admin/chat/reports/mail/{rep}/delete").json()["ok"] and "removed by a moderator" in other.get(B + f"/my/messages/{vid}").text and psql("select count(*) from mail_reports where handled_at is null") == "0")

# shared pages
au = adm.get(B + "/admin/audit").text
check("sending, deleting for good and reporting are in the audit log, without the words", all(x in au for x in ("mail.send", "mail.delete", "mail.report")) and "half written" not in au)
setting("access_messages", "roles")
check("with Messages limited to roles a member without the permission sees none of it", me.get(B + "/my/messages").status_code == 404 and me.get(B + "/chat/").status_code == 200)
unset("access_messages")
setting("pm_enabled", "0")
check("with Messages off the pages and the menu link are gone", me.get(B + "/my/messages").status_code == 404 and 'href="/my/messages"' not in me.get(B + "/").text)
unset("pm_enabled")
check("the settings are in Admin > Settings", all(x in adm.get(B + "/admin/settings").text for x in ("Messages: Longest Subject", "Messages: Most Recipients", "Messages: Days Deleted Messages Stay in the Trash", "Messages: Messages per Page", "Who Can See Private Messages")))
wipe()
unset(*KEYS)
print("ALL PASS" if ok_all else "SOME FAILED")

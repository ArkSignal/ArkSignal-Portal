"""Emergency commands, run on the server:  docker exec -it adsb-portal python -m app.cli <command> <username>

  reset-2fa <user>      turn off two-factor sign-in for someone (lost phone and recovery codes, or the SECRET_KEY changed)
  unlock <user>         clear a lockout from too many failed sign-ins
  set-password <user>   set a new password (asks for it twice; the person must change it at their next sign-in)
  clear-admin-allow     empty Settings > Security > Admin Area: Allowed Addresses (a change of address locked everyone out)
"""
import getpass
import os
import sys

os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"   # no background jobs for a one-off command


def main(argv):
    if len(argv) == 2 and argv[1] == "clear-admin-allow":
        from . import create_app
        from .settings import set_setting
        with create_app().app_context():
            set_setting("admin_allow_addresses", "")
        print("The Admin area's allowed-address list is empty: the Admin area is open to administrators from anywhere.")
        return 0
    if len(argv) != 3 or argv[1] not in ("reset-2fa", "unlock", "set-password"):
        print(__doc__)
        return 2
    from . import create_app
    from .db import get_db
    from .models import User
    app = create_app()
    with app.app_context():
        user = User.get_by_username(argv[2])
        if user is None:
            print(f"No account named {argv[2]}.")
            return 1
        d = get_db()
        if argv[1] == "reset-2fa":
            d.execute("UPDATE users SET totp_secret='', totp_enabled=FALSE, totp_last=0 WHERE id=%s", (user.id,))
            d.execute("DELETE FROM recovery_codes WHERE user_id=%s", (user.id,))
            d.commit()
            print(f"Two-factor sign-in is off for {user.username}.")
        elif argv[1] == "unlock":
            d.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=%s", (user.id,))
            d.commit()
            print(f"{user.username} is unlocked.")
        else:
            a, b = getpass.getpass("New password: "), getpass.getpass("Again: ")
            if a != b:
                print("The two entries differ.")
                return 1
            from . import security
            err = security.password_error(a)
            if err:
                print(err)
                return 1
            user.set_password(a, must_change=True)
            print(f"New password set for {user.username}; they must change it at their next sign-in.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

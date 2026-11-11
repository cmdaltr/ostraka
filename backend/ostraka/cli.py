"""`ostraka` command line, for what can't be done from the browser.

    ostraka bootstrap-admin EMAIL [--force] [--link-only]
    ostraka has-admin                       true or false: is there an active admin?
    ostraka import FILE | --sample          load records through the API (see ostraka/importer.py)
    ostraka import --remove FILE | --remove-sample
    ostraka licence [show] | remove                   the edition and places taken; back to free
    ostraka licence install FILE --accept-terms "Name <email>"

ostraka has no default account. The first admin comes from this command, run on the server
(`docker compose exec backend ostraka bootstrap-admin you@example.com`), which prints a one-time
invitation link. It refuses while an active admin exists unless --force is given. --link-only prints
the link and nothing else, for the host `ostraka first-admin` and the desktop launchers that open it.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from email_validator import EmailNotValidError, validate_email
from sqlalchemy import update

from ostraka import accounts, audit, importer, licence
from ostraka.db import sessions
from ostraka.models import Invitation, Role, utcnow

# bootstrap-admin's exit when there's an admin already: the launchers stop asking for one only on this
ADMIN_EXISTS = 5


async def _bootstrap_admin(email: str, force: bool, link_only: bool = False) -> int:
    async with sessions()() as db:
        admins = await accounts.active_admins(db)
        if not force and admins > 0:
            print("Ostraka already has an admin. Ask them to invite you, or use --force.", file=sys.stderr)  # noqa: T201
            return ADMIN_EXISTS
        # Getting back in when no admin is left always works; adding one beside them takes a place
        if admins and (problem := await accounts.seat_refusal(db, Role.ADMIN)):
            print(problem, file=sys.stderr)  # noqa: T201
            return 1
        # Only the newest bootstrap link works, whatever email the earlier ones were for: otherwise each
        # run would leave another live admin invitation behind
        await db.execute(
            update(Invitation)
            .where(
                Invitation.invited_by.is_(None),
                Invitation.role == Role.ADMIN,
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
            )
            .values(revoked_at=utcnow())
        )
        _, link = await accounts.create_invitation(
            db, email=email, role=Role.ADMIN, team_id=None, invited_by=None
        )
        audit.record(db, "admin_bootstrapped", email=email)
        await db.commit()
    if link_only:
        print(link)  # noqa: T201
    else:
        print(f"Invitation for {email} (admin). Open within the next few days:\n\n  {link}\n")  # noqa: T201
    return 0


async def _has_admin() -> int:
    """Prints true or false, for `ostraka status --json`'s has_admin: the launchers ask for the first
    admin's email only when there's none."""
    async with sessions()() as db:
        admins = await accounts.active_admins(db)
    print("true" if admins > 0 else "false")  # noqa: T201
    return 0


async def _import(path: Path, remove: bool) -> int:
    try:
        if remove:
            records, people, teams = await importer.remove(path)
            print(f"Removed {records} records, {people} people and {teams} teams loaded from {path.name}.")  # noqa: T201
            return 0
        outcome = await importer.run(path)
    except importer.ImportProblem as exc:
        print(f"Nothing more was loaded: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    print(  # noqa: T201
        f"Loaded {', '.join(outcome.records)} ({outcome.steps} steps). "
        f"{outcome.people_created} people and {outcome.teams_created} teams were new; "
        "imported people can't sign in."
    )
    return 0


async def _licence_show() -> int:
    async with sessions()() as db:
        standing = await licence.current(db)
        held, _ = await accounts.places_held(db)
    lic = standing.licence
    print(f"Edition: {standing.edition.value} ({standing.state})")  # noqa: T201
    if lic:
        print(f"Licensed to {lic.licensee} ({lic.licence_id}), runs to {lic.expires or 'no end date'}")  # noqa: T201
        print(f"Features: {', '.join(sorted(standing.features)) or 'none'}")  # noqa: T201
    print(f"Places: {held} of {standing.seats or 'unlimited'} taken")  # noqa: T201
    return 0


async def _licence_install(path: Path, accepted_by: str) -> int:
    """From the server, for when no admin can sign in to do it. Recorded like an admin's install,
    with the name given for whoever accepted the terms."""
    text = path.read_text().strip()
    if not accepted_by.strip():
        version = licence.terms()[0]
        print(  # noqa: T201
            f"Installing a licence accepts the Ostraka Commercial Licence, version {version}, on the "
            "licensee's behalf. Read COMMERCIAL-LICENSE.md, then run this again with "
            '--accept-terms "Your Name <you@example.org>".',
            file=sys.stderr,
        )
        return 1
    try:
        lic = licence.parse(text)
    except licence.LicenceProblem as exc:
        print(exc, file=sys.stderr)  # noqa: T201
        return 1
    async with sessions()() as db:
        await licence.record_install(db, text, accepted_by=None, accepted_by_name=accepted_by.strip())
        audit.record(
            db,
            "licence_installed",
            licence=lic.licence_id,
            edition=lic.edition.value,
            licensee=lic.licensee,
            seats=lic.seats,
            expires=lic.expires.isoformat() if lic.expires else "perpetual",
            terms_version=licence.terms()[0],
            accepted_by=accepted_by.strip(),
            via="cli",
        )
        await db.commit()
    seats = lic.seats or "unlimited"
    print(
        f"Installed {lic.edition.value} for {lic.licensee}: {seats} seats to {lic.expires or 'no end date'}."
    )  # noqa: T201
    return 0


async def _licence_remove() -> int:
    """Back to the free edition. Nobody is deactivated."""
    async with sessions()() as db:
        row = await licence.installed(db)
        if row is None:
            print("No licence is installed.")  # noqa: T201
            return 0
        row.removed_at = utcnow()
        audit.record(db, "licence_removed", via="cli")
        await db.commit()
    print("Removed. Ostraka is on the free edition; nobody has been deactivated.")  # noqa: T201
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ostraka")
    commands = parser.add_subparsers(dest="command", required=True)
    boot = commands.add_parser("bootstrap-admin", help="print a one-time link to create an admin account")
    boot.add_argument("email")
    boot.add_argument("--force", action="store_true", help="even if an active admin already exists")
    boot.add_argument("--link-only", action="store_true", help="print only the link, for a launcher to open")
    commands.add_parser("has-admin", help="print true or false: whether an active admin exists")
    load = commands.add_parser("import", help="load incidents and hunts from a ostraka-import/1 file")
    which = load.add_mutually_exclusive_group(required=True)
    which.add_argument("file", nargs="?", type=Path, help="the file to load")
    which.add_argument("--sample", action="store_true", help="the sample: four incidents and three hunts")
    which.add_argument("--remove", type=Path, metavar="FILE", help="remove what FILE loaded")
    which.add_argument("--remove-sample", action="store_true", help="remove the sample")
    lic = commands.add_parser("licence", help="show the edition, or install a licence file")
    lic_do = lic.add_subparsers(dest="action")
    lic_do.add_parser("show", help="the edition and how many places are taken")
    put = lic_do.add_parser("install", help="install a licence from a file")
    put.add_argument("file", type=Path)
    put.add_argument(
        "--accept-terms",
        metavar="NAME",
        default="",
        help='accept the commercial terms for the licensee, as "Name <email>"',
    )
    lic_do.add_parser("remove", help="remove the licence: back to the free edition")
    args = parser.parse_args(argv)

    if args.command == "bootstrap-admin":
        try:
            email = validate_email(args.email, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            parser.error(str(exc))
        return asyncio.run(_bootstrap_admin(email, args.force, args.link_only))
    if args.command == "has-admin":
        return asyncio.run(_has_admin())
    if args.command == "import":
        if args.remove or args.remove_sample:
            return asyncio.run(_import(args.remove or importer.SAMPLE, remove=True))
        return asyncio.run(_import(importer.SAMPLE if args.sample else args.file, remove=False))
    if args.command == "licence":
        if args.action == "install":
            return asyncio.run(_licence_install(args.file, args.accept_terms))
        if args.action == "remove":
            return asyncio.run(_licence_remove())
        return asyncio.run(_licence_show())
    return 2


if __name__ == "__main__":
    sys.exit(main())

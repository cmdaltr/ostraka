# Licensing model

Ostraka, Shikari and Rowanholt share one licensing model (Ben, 11 November 2026). The first part of
this file is that shared specification, kept identical in each product's `docs/LICENSING_MODEL.md`;
the second is Ostraka's own split. Ostraka is the reference implementation. The canonical copy of
the shared part, the issued licences and the prompts for applying it are in the private
`cmdaltr/licensing` repo.

## Shared across Ostraka, Shikari and Rowanholt

### Code licence and notices

- **Elastic License 2.0** for all three (`LICENSE`: the ELv2 text, after a line naming the licensor,
  Ben Smith (cmdaltr)). Free to use, copy and change; no providing it to others as a hosted or
  managed service; no getting around the licence key. Not open core: the key's protection is in the
  code licence itself, not goodwill.
- **`COMMERCIAL-LICENSE.md`** adds the Pro and Services terms, governed by the law of England and
  Wales, marked for legal review. Services is where ELv2's managed-service restriction is lifted for
  work done for the licensee's own clients.
- Copyright **cmdaltr**; a ™ notice for each product's name and logo; outside contributions need a
  contributor licence agreement.
- Releases already published under another licence (Rowanholt's MIT, Shikari's AGPL) keep it; ELv2
  applies from the first release that ships it.

### Editions

| Edition | For | Seats | Always includes |
|---|---|---|---|
| **Free** | Individuals and small teams | The product's own free number | The whole core product: reports in the product's own style, no automation |
| **Pro** | Organisations using it for themselves | As the licence states, or unlimited | Free, plus `report-branding` and `automation` |
| **Services** | MSSPs and consultancies working for clients | As the licence states, or unlimited | Pro, plus `brand-library` and the `managed-service` right |

The features are the same in every product (Ben, 11 November 2026); only the free seat number
differs:

| Feature | Edition | What it is |
|---|---|---|
| `report-branding` | Pro | One brand (colours and logos) for the installation's reports, in place of the product's own style |
| `brand-library` | Services | A brand per client, chosen for each piece of work the report comes from |
| `automation` | Pro | Signed webhooks: the product calls the customer's systems when something happens. Checked when a webhook is sent, so one configured on Free is kept but doesn't fire |
| `managed-service` | Services | The right in `COMMERCIAL-LICENSE.md` section 3 to run the product for clients. Not a switch |

Webhooks work the same everywhere, as Shikari's (`shikari/webhooks.py`, the reference for them): an
admin registers a URL and the events it wants; each delivery is a POST of
`{"event", "sent_at", "data"}` with `X-<Product>-Event` and, when the webhook has a secret,
`X-<Product>-Signature: sha256=<hex HMAC-SHA256 of the raw body>`; three tries with backoff; the last
20 deliveries kept per webhook for the admin page. Webhook bodies pass the same masking as the
product's other outward messages.

### The licence

One line: `<product>-licence-1.<payload>.<signature>`.

- `payload` is base64url (no padding) of canonical JSON: sorted keys, compact separators.
- `signature` is Ed25519, base64url, over the ASCII bytes of `<product>-licence-1.<payload>`, so
  the product and format version are signed too.
- Payload fields: `licence_id`, `product`, `licensee`, `edition` (`pro` or `services`), `seats` (a
  number, or `null` for unlimited), `issued`, `expires` (a date, or `null` for perpetual), and
  optionally `email`, `features` (extra feature ids, or `*` for all) and `not_before`. Dates are
  `YYYY-MM-DD`.
- A licence for another product, another format version, or with a bad signature is refused with a
  reason an admin can act on.

### Terms and accepting them

- `COMMERCIAL-LICENSE.md` states its version on the line after its title (`**Version 1.0**, date`).
  The licensor is "Ben Smith, trading as cmdaltr (cmdaltr.com)" until there's a company. An order
  wins over the terms, the terms over ELv2.
- The product ships a copy of the terms where its server can read them (Ostraka:
  `backend/ostraka/legal/`), and a test keeps the copy identical to the root file.
- **Installing a licence needs an admin to accept the current terms on the licensee's behalf.** The
  admin page checks the licence first (showing the licensee, edition, seats and dates), shows the
  terms, and installs only once "I accept the <product> Commercial Licence, version x.y, on behalf
  of <licensee>, and I have authority to do so" is ticked. The API refuses an install without
  `accept_terms` (422) or with a `terms_version` that isn't current (409).
- The installed licence keeps the terms version, who accepted (the account, and "Name <email>" as
  written) and when; the admin page shows it, and the install's audit entry carries the version.
  The CLI needs `--accept-terms "Name <email>"`.
- Every version a customer has accepted is filed in the licensing repo under
  `terms/<product>/COMMERCIAL-LICENSE-v<version>.md`. A new version applies from the next key a
  customer installs.

### Keys and tooling

- **One signing key per product**, so a leak affects one product only. Only the public half ships,
  as hex in `PUBLIC_KEYS` (a tuple, so a key can be rotated by adding the new one). Licences are
  checked offline; nothing calls home.
- `scripts/admin/sign_licence.py` with `keygen PATH`, `public` and `sign`, reading the private key
  from `<PRODUCT>_LICENCE_KEY` (a file path or the key itself; PEM, or the seed as hex).
- `scripts/admin/issue-licence.sh "Licensee" <pro|services> <seats|unlimited> [email|-]
  [YYYY-MM-DD|perpetual] [features]`.
- **The ledger:** with `LICENCE_LEDGER_DIR` set to the licensing repo, `sign` writes the licence to
  `issued/<product>/<licence_id>.licence` and appends a row to `ledger.csv` (licence_id, product,
  licensee, email, edition, seats, features, issued, not_before, expires, terms_version, order,
  file, notes). Without it, the licence goes to a git-ignored `licences/`. The signed order form for
  a sale goes in the licensing repo's `orders/`.
- `.gitignore` covers `licences/`, `*licence-signing-key*`, `*licence-signing.key`, `*.lic.key` and
  `*.licence`. Never commit a private key or an issued licence.

### Behaviour

- **Entitlements:** the edition's own features, plus any the licence lists; `*` grants every
  feature. Code asks `has(feature)`, never the edition name.
- **Check at the point of use**, never at configuration time, so editing stored settings can't get
  around it.
- **Seats** count accounts that can sign in plus open invitations that would add one, and are
  checked only when someone is added (invited, re-enabled, given access). Never retroactively.
- **Validity:** from the start of `not_before` (if any) to the end of `expires`. Before
  `not_before`: Free, shown as pending. After `expires`: the edition carries on for **30 days'
  grace** with a warning to admins, then the installation is Free again.
- **Nobody is ever locked out.** A licence ending, being removed or failing to verify never
  deactivates anyone or deletes anything; it only stops new additions past the free limit and turns
  paid features off.
- **Admin surface:** an admin-only page to see the edition, places taken and who accepted the terms,
  and to check, accept and install or remove a licence; `<product> licence show|install FILE
  --accept-terms NAME|remove`. Installs and removals are recorded where the
  product keeps its security log.
- **Tests** sign with a throwaway key (patching `PUBLIC_KEYS`) and cover: valid, tampered, another
  key, another product, not-yet-valid, grace then lapse, perpetual, wildcard, edition features, the
  seat limit, install refused without acceptance or with a stale terms version, acceptance recorded,
  and the shipped terms identical to `COMMERCIAL-LICENSE.md`.

### Individual to each product

Where the licence is stored (Ostraka: its database; a product without one: a file); the free seat
number; what a client's brand attaches to (Ostraka: an incident); which events send webhooks; how
"an admin" is spotted for the seat rule.

## Ostraka's split

| Feature id | Free | Pro | Services | Gated at |
|---|---|---|---|---|
| (seats) | 4, one an admin or team lead | Licence | Licence | `accounts.seat_refusal`: invitations, re-enabling, access for imported people, `bootstrap-admin` beside a live admin |
| `report-branding` | Ostrakon | One brand for the installation | Yes | `reports.brands.styling_for`, and brand writes in `api/installation.py` |
| `brand-library` | | | A brand per client, chosen per incident | `styling_for`, brand creation past the first, `api/incidents._choose_brand` |
| `automation` | Kept, not sent | Webhooks, and acting on Shikari's | Webhooks, and acting on Shikari's | `webhooks._send_all` and the test send in `api/webhooks.py`; registering and listing are open on every edition. Acting on Shikari's signed `run.completed` webhook (`api/integrations.shikari_webhook`, `integrations.gaps._recheck_all`); the Rowanholt link, detection gaps and asking Shikari from an incident are Free |
| `managed-service` | | | Run Ostraka for clients | A right in `COMMERCIAL-LICENSE.md`, not a switch |

Webhook events come from the activity log, after the change commits (`activity.finish` →
`webhooks.queue`): `incident.created` (declared), `incident.status_changed` (a phase move or a
reopening), `incident.closed`, `task.assigned` (a task added, given to someone else, or claimed) and
`hunt.completed` (a hunt closed). Bodies pass `notify.outward_data`: indicators masked, and a TLP:RED
or TLP:AMBER+STRICT record sent as `{"ref"}` only. URLs are https, or http to localhost and RFC 1918
addresses; never link-local. Webhooks and their last 20 deliveries are in the database (migration
0013), admin only, and every change is in the security log.

Free on purpose: everything that makes Ostraka an incident record (incidents and hunts, tasks and
playbooks, findings, assessments, ACH, lessons, the activity chain), both reports in PDF and Word,
notifications (push, email, Teams), the MCP server, the importer, and AI rephrasing with the TLP
guard. The reports must be good unbranded for branding to be worth buying.

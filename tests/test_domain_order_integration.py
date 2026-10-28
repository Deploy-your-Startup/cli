"""GIVEN a local KonsoleH order flow, WHEN `startup hetzner domain` runs in a real
browser, THEN it picks the owner and only places the binding order when told to."""

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs

import pytest
from click.testing import CliRunner

from cli.hetzner import config
from cli.startup import cli

CONTACTS = [
    {"first_name": "Alex", "surname": "Sample", "emails": "alex@example.com"},
    {"first_name": "Alex", "surname": "Sample", "emails": "office@example.com"},
]

ORDER_PAGE = """<html><body>
<form id="orderform" method="post" action="/order.php">
  <input type="hidden" name="step" id="step" value="1">
</form>
<script>
function selectProduct(product, id, step) {
  document.getElementById('step').value = '2';
  document.getElementById('orderform').submit();
}
</script></body></html>"""

LOOKUP_PAGE = """<html><body>
<form id="domain_lookup_form" method="post" action="/order.php">
  <input type="hidden" name="step" value="3">
  <input type="radio" id="transfer_no" name="transfer" value="no">
  <input id="search_domain" name="domain">
  <select id="tld_select" name="tld"><option value=".de">.de</option></select>
  <input type="checkbox" id="enable_nameserver" name="own_ns">
  <input id="nameserver1" name="ns1"><input id="nameserver2" name="ns2">
  <input id="nameserver3" name="ns3">
  <button type="submit" id="next_button">Weiter</button>
</form></body></html>"""


def owner_page(contacts):
    options = "".join(
        f'<option value="">{c["first_name"]} {c["surname"]} (Person)</option>'
        for c in contacts
    )
    fills = ",".join(
        "{" + ",".join(f"{key}: '{value}'" for key, value in c.items()) + "}"
        for c in contacts
    )
    return f"""<html><body>
<form id="contacts" method="post" action="/order.php">
  <input type="hidden" name="step" value="4">
  <input type="hidden" name="owner_index" id="owner_index">
  <select id="domain_owners" name="domain_owners">
    <option value="chooseContact" selected>Bitte Kontakt wählen</option>
    <optgroup label="----">{options}</optgroup>
    <option value="createNew">Neue Kontaktadresse</option>
  </select>
  <input id="first_name"><input id="surname"><input id="emails"><input id="city">
  <button type="button" id="btnNextStep" disabled>Weiter</button>
</form>
<script>
const contacts = [{fills}];
const select = document.getElementById('domain_owners');
select.addEventListener('change', () => {{
  const contact = contacts[select.selectedIndex - 1] || {{}};
  for (const field of ['first_name', 'surname', 'emails']) {{
    document.getElementById(field).value = contact[field] || '';
  }}
  document.getElementById('city').value = contact.emails ? 'Sampletown' : '';
  document.getElementById('btnNextStep').disabled = select.value === 'chooseContact';
}});
document.getElementById('btnNextStep').addEventListener('click', () => {{
  document.getElementById('owner_index').value = select.selectedIndex;
  document.getElementById('contacts').submit();
}});
</script></body></html>"""


def summary_page(contact):
    return f"""<html><body>
<form method="post" name="billing_form" action="/order.php">
  <input type="hidden" name="step" value="13">
  <table class="order-summary">
    <tr><td>.de Domainregistrierung:</td><td class="prices">
      <span class="pricePeriod">jährlich</span><span class="priceFull">5,83 €</span></td></tr>
    <tr><td>Domain:</td><td>sample.de</td></tr>
    <tr><td>Kontaktperson:</td><td>{contact["first_name"]} {contact["surname"]}</td></tr>
    <tr><td>E-Mailadresse:</td><td>{html.escape(contact["emails"])}</td></tr>
  </table>
  <input type="checkbox" name="accept_terms_and_conditions" id="accept_terms_and_conditions">
</form>
<a class="btn btn-primary" href="#"
   onclick="document.forms.billing_form.submit();return false;">Zahlungspflichtig bestellen</a>
</body></html>"""


@pytest.fixture
def konsoleh(monkeypatch, tmp_path):
    """A stand-in for Hetzner's KonsoleH order pages, reached over real HTTP."""
    state: dict[str, Any] = {"contacts": list(CONTACTS), "owner": None, "orders": []}

    class KonsoleH(BaseHTTPRequestHandler):
        def respond(self, body):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            self.respond(ORDER_PAGE)

        def do_POST(self):
            length = int(self.headers["Content-Length"])
            form = {
                key: values[0]
                for key, values in parse_qs(self.rfile.read(length).decode()).items()
            }
            step = form["step"]
            if step == "2":
                self.respond(LOOKUP_PAGE)
            elif step == "3":
                state["domain"] = form["domain"] + form["tld"]
                self.respond(owner_page(state["contacts"]))
            elif step == "4":
                state["owner"] = state["contacts"][int(form["owner_index"]) - 1]
                self.respond(summary_page(state["owner"]))
            elif "accept_terms_and_conditions" not in form:
                self.respond(summary_page(state["owner"]))
            else:
                state["orders"].append((state["domain"], state["owner"]["emails"]))
                self.respond("<html><body><h2>Vielen Dank</h2></body></html>")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), KonsoleH)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(config, "KONSOLEH_BASE_URL", root)
    monkeypatch.setattr(config, "KONSOLEH_ORDER_URL", root + "/order.php")
    # Playwright's bundled Chromium with a throwaway profile replaces the
    # user's Chrome; the browser itself is real.
    monkeypatch.setenv("STARTUP_BROWSER", "chromium")
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", tmp_path / "profile")
    monkeypatch.setattr(config, "ORDER_WAIT_TIMEOUT", 3000)
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def order(*args):
    return CliRunner().invoke(cli, ["hetzner", "domain", "--headless", *args])


def test_confirmed_order_uses_the_chosen_contact(konsoleh):
    # GIVEN two contacts with the same name but different e-mails.
    # WHEN the user names the owner and confirms the order up front.
    result = order(
        "--contact-email", "office@example.com", "--confirm-order", "sample.de"
    )
    # THEN exactly one order is placed for that owner after showing its price.
    assert result.exit_code == 0, result.output
    assert konsoleh["orders"] == [("sample.de", "office@example.com")]
    assert "Domain owner: Alex Sample <office@example.com>" in result.output
    assert "Price:  5,83 € (jährlich)" in result.output
    assert "Press Enter" not in result.output


def test_ambiguous_owner_stops_before_any_order(konsoleh):
    # GIVEN two possible owners and no terminal to ask.
    # WHEN the order is started without naming a contact.
    result = order("--confirm-order", "sample.de")
    # THEN the CLI names the contacts to choose from and orders nothing.
    assert result.exit_code == 1
    assert "alex@example.com, office@example.com" in result.output
    assert "--contact-email" in result.output
    assert konsoleh["orders"] == []


def test_unknown_contact_is_rejected(konsoleh):
    # GIVEN the existing contacts.
    # WHEN the user names an e-mail no contact uses.
    result = order("--contact-email", "nobody@example.com", "sample.de")
    # THEN the order stops before the summary.
    assert result.exit_code == 1
    assert "No KonsoleH contact uses nobody@example.com" in result.output
    assert konsoleh["owner"] is None


def test_unconfirmed_order_waits_for_the_browser(konsoleh):
    # GIVEN a single contact, chosen automatically.
    konsoleh["contacts"] = CONTACTS[:1]
    # WHEN the order runs unattended without --confirm-order and nobody orders.
    result = order("sample.de")
    # THEN the summary is shown, nothing is ordered and the wait times out.
    assert result.exit_code == 1
    assert "Domain owner: Alex Sample <alex@example.com>" in result.output
    assert "Price:  5,83 € (jährlich)" in result.output
    assert "No order was placed before the timeout." in result.output
    assert konsoleh["orders"] == []

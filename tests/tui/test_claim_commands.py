from types import SimpleNamespace

from hardy.app.tui import handlers
from hardy.app.tui.ports import State
from hardy.workflows.interactive.claims import ClaimRef, ClaimService


class Session:
    def __init__(self):
        self.state = {}
        self.events = []
        self.claims = ClaimService(self.state, lambda: None, self.events.append)


async def test_claim_commands_create_list_show_revise_and_frontier(ui):
    state = State(config=SimpleNamespace(), session=Session())
    await handlers.handle_claim(ui, "new Main target", state)
    await handlers.handle_claim(ui, "new Supporting lemma", state)
    state.session.claims.add_dependency(
        ClaimRef(claim_id="C1", revision=1),
        ClaimRef(claim_id="C2", revision=1),
    )
    await handlers.handle_claim(ui, "revise C2 Better supporting lemma", state)
    await handlers.handle_claim(ui, "C1", state)
    await handlers.handle_claims(ui, "", state)
    await handlers.handle_frontier(ui, "", state)
    assert "Created C1@r1" in ui.text
    assert "C2@r1" in ui.text
    assert "C2@r2" in ui.text
    assert "Research frontier" in ui.text


def test_registry_exposes_claim_surface():
    names = {command.name for command in handlers.build_registry()}
    assert {"claim", "claims", "frontier", "prove"} <= names

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from expand_benefitlens_public_boards import published_board_tokens


def test_literal_board_bindings_are_required():
    raw = b'''<a href="https://boards.greenhouse.io/figma/jobs/123">Apply</a>
    <script>const p='https://api.greenhouse.io/v1/boards/robinhood/jobs';</script>
    <script>var CONFIG={boardToken:'moloco'}; var p='https://boards-api.greenhouse.io/v1/boards/'</script>'''
    assert published_board_tokens(raw) == ['figma', 'moloco', 'robinhood']
    assert published_board_tokens(b'<h1>Acme careers</h1>') == []
    assert published_board_tokens(b'<script>var boardToken="not-a-verified-api"</script>') == []


import os, json
os.environ["DISABLE_BACKUP_SCHEDULER"] = "1"
from app import create_app
app = create_app()
from app import bridge_verify as bv
out = {}
T = [("John Public", "JOHN Q PUBLIC", "normal", True), ("John Q. Public Jr.", "John Q Public", "normal", True), ("Public, John", "JOHN PUBLIC", "normal", True), ("Johnny Public", "JOHN PUBLIC", "normal", False),
     ("Johnny Public", "JOHN PUBLIC", "loose", True), ("John Smith", "JOHN PUBLIC", "loose", False), ("John Q Public", "JOHN PUBLIC", "strict", True), ("John Public", "JOHN R PUBLIC", "strict", True),
     ("John Public Smith", "JOHN PUBLIC", "strict", False), ("Marty", "MARTIN ALLISON", "normal", False), ("J Public", "JOHN PUBLIC", "normal", False), ("Mary-Ann O'Neil", "MARY ANN ONEIL", "normal", True)]
out["names"] = [bv.names_match(a, b, m)[0] == want for a, b, m, want in T]
with app.app_context():
    out["recheck"] = bv.recheck_all()
print(json.dumps(out))

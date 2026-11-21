from common import *
import io
from investos.check.cli import main
from investos.core.db import open_db
from investos.core import log
log.configure_logging(open(os.devnull,"w"))
root, cfg = make_root(); open_db(cfg).close()
raw = cfg.zones["raw"]
(raw/"BSE"/"aa").mkdir(parents=True); (raw/"BSE"/"aa"/"junk").write_bytes(b"x"); os.chmod(raw/"BSE"/"aa"/"junk", 0o666)
(raw/"stray.bin").write_bytes(b"y"); os.chmod(raw/"stray.bin", 0o666)
(raw/"nse-x").mkdir(); (raw/"nse-x"/"f").write_bytes(b"z")
(cfg.zones["manifests"]/"Bad-Name.jsonl").write_bytes(b"garbage no newline")
b = io.StringIO(); rc = main(["all"], cfg=cfg, stdout=b); print("rc", rc); print(b.getvalue())

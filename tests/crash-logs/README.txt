Server crash-log samples for the modpack auto-fix tests (tests/test_autofix.py).

Real excerpts (trimmed, timestamps/prefixes shortened) from public reports:
  forge-1.19.2-oculus.txt          itzg/docker-minecraft-server issue 1893
  forge-1.18.2-torohealth.txt      itzg/docker-minecraft-server issue 1893 (comment)
  forge-1.20.1-skyboxes.txt        itzg/docker-minecraft-server issue 3405
  forge-1.20.1-mixin-only.txt      itzg/docker-minecraft-server issue 3912
  forge-1.19.2-missing-dep.txt     itzg/docker-minecraft-server issue 2730
  forge-1.20.1-java25.txt          itzg/docker-minecraft-server issue 3912 (not a client mod: wrong Java)
  fabric-1.20.1-random.txt         itzg/docker-minecraft-server issue 2870 (not a client mod)

Made to match Fabric Loader's message format, NOT a real log (replace when we get one):
  fabric-format-client-class.txt

To add a new case: save the crash lines here, then add a line to the CASES table in test_autofix.py.
If the auto-fix should catch it, add a rule to CLIENT_CLUES / CLIENT_MARKERS in pi5/decklib.py.

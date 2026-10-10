"""Lance l'interface V2 de SeaTrace (Flask + carte Leaflet), en local.

    python serveur.py              -> http://127.0.0.1:8050
    python serveur.py --port 8080

L'ancienne interface Streamlit (V1) reste disponible : streamlit run app.py
"""

import argparse

from web.app import creer_app

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--hote", default="127.0.0.1", help="adresse d'écoute (défaut : locale uniquement)")
    ap.add_argument("--port", type=int, default=8050)
    args = ap.parse_args()
    print(f"SeaTrace V2 : http://{args.hote}:{args.port}  (Ctrl+C pour arrêter)")
    creer_app().run(host=args.hote, port=args.port, debug=False, threaded=True)

"""
Polymarket Trade Tracker - Bot Telegram complet
- Surveille les wallets via data-api.polymarket.com
- Résumé automatique toutes les 6h
- Commande /add pour ajouter des wallets depuis Telegram
- Serveur HTTP health check pour Render
"""
import asyncio
import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
from collections import defaultdict, Counter
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from dotenv import load_dotenv


# ==========================================
# HEALTH CHECK SERVER (pour Render)
# ==========================================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Polymarket Bot is running")

    def log_message(self, format, *args):
        pass  # Pas de logs HTTP


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"Health check server on port {port}")

load_dotenv()

# Configuration
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
DEST_CHANNEL = os.getenv('TELEGRAM_CHAT_ID', '@poly_kitti_print')
DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"
CHECK_INTERVAL = 30
SUMMARY_INTERVAL_HOURS = 1

# Fichier pour stocker les wallets (persistant)
WALLETS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'wallets.json')


def load_wallets() -> dict:
    """Charge les wallets depuis le fichier JSON"""
    wallets = {}

    # D'abord charger depuis .env
    _raw = os.getenv('TRACKED_WALLETS', '').replace(',', ' ').split()
    for w in _raw:
        w = w.strip().lower()
        if w.startswith('0x'):
            wallets[w] = f"Wallet {w[:8]}"

    # Puis charger depuis le fichier JSON (écrase les noms si existants)
    if os.path.exists(WALLETS_FILE):
        try:
            with open(WALLETS_FILE, 'r') as f:
                saved = json.load(f)
                wallets.update(saved)
        except:
            pass

    return wallets


def save_wallets(wallets: dict):
    """Sauvegarde les wallets dans le fichier JSON"""
    with open(WALLETS_FILE, 'w') as f:
        json.dump(wallets, f, indent=2)


class PolymarketTracker:
    def __init__(self):
        self.wallets = load_wallets()  # {address: name}
        self.seen_txs = set()
        self.last_summary = datetime.utcnow()
        self.app = None
        print(f"✅ Tracker initialisé avec {len(self.wallets)} wallets")
        for addr, name in self.wallets.items():
            print(f"   - {name}: {addr[:10]}...{addr[-4:]}")

    # ==========================================
    # TELEGRAM BOT COMMANDS
    # ==========================================

    async def cmd_add(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/add 0xADRESSE nom_du_wallet"""
        args = context.args
        if len(args) < 1:
            await update.message.reply_text(
                "Usage: /add 0xADRESSE nom_du_wallet\n"
                "Exemple: /add 0xbb015bb...e5 WhaleAlpha"
            )
            return

        address = args[0].strip().lower()
        name = " ".join(args[1:]) if len(args) > 1 else f"Wallet {address[:8]}"

        if not address.startswith('0x') or len(address) < 10:
            await update.message.reply_text("❌ Adresse invalide. Elle doit commencer par 0x")
            return

        self.wallets[address] = name
        save_wallets(self.wallets)

        await update.message.reply_text(
            f"✅ Wallet ajouté!\n"
            f"📛 Nom: {name}\n"
            f"👛 Adresse: {address[:10]}...{address[-4:]}\n"
            f"📊 Total wallets: {len(self.wallets)}"
        )
        print(f"➕ Wallet ajouté: {name} ({address[:10]}...)")

    async def cmd_remove(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/remove 0xADRESSE"""
        args = context.args
        if len(args) < 1:
            await update.message.reply_text("Usage: /remove 0xADRESSE")
            return

        address = args[0].strip().lower()
        if address in self.wallets:
            name = self.wallets.pop(address)
            save_wallets(self.wallets)
            await update.message.reply_text(f"✅ Wallet supprimé: {name}")
        else:
            await update.message.reply_text("❌ Wallet non trouvé")

    async def cmd_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/list - Liste les wallets surveillés"""
        if not self.wallets:
            await update.message.reply_text("Aucun wallet surveillé")
            return

        lines = [f"📋 Wallets surveillés ({len(self.wallets)}):\n"]
        for i, (addr, name) in enumerate(self.wallets.items(), 1):
            lines.append(f"{i}. {name}\n   {addr[:10]}...{addr[-4:]}")

        await update.message.reply_text("\n".join(lines))

    async def cmd_summary(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/summary - Génère un résumé immédiat"""
        await update.message.reply_text("⏳ Analyse en cours...")
        summary = await self.generate_wallet_summary()
        if summary:
            await self.send_telegram(summary)
        else:
            await update.message.reply_text("⚠️ Aucune donnée trouvée")

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/start - Message de bienvenue"""
        await update.message.reply_text(
            "🤖 Polymarket Wallet Tracker\n\n"
            "Commandes:\n"
            "/add 0xADRESSE nom - Ajouter un wallet\n"
            "/remove 0xADRESSE - Supprimer un wallet\n"
            "/list - Liste des wallets\n"
            "/summary - Résumé immédiat\n"
            "/help - Aide"
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """/help"""
        await update.message.reply_text(
            "📖 Aide:\n\n"
            "➕ Ajouter un wallet:\n"
            "/add 0xbb015bb...e5 WhaleAlpha\n\n"
            "➖ Supprimer un wallet:\n"
            "/remove 0xbb015bb...e5\n\n"
            "📋 Voir les wallets:\n"
            "/list\n\n"
            "📊 Résumé immédiat:\n"
            "/summary\n\n"
            f"⏰ Résumé auto toutes les {SUMMARY_INTERVAL_HOURS}h"
        )

    # ==========================================
    # API POLYMARKET
    # ==========================================

    def get_wallet_activity(self, wallet: str, limit: int = 50):
        """Récupère l'activité via data-api.polymarket.com"""
        try:
            resp = requests.get(
                f"{DATA_API}/activity",
                params={"user": wallet, "limit": limit},
                timeout=15
            )
            if resp.status_code == 200:
                return resp.json()
        except:
            pass
        return []

    async def send_telegram(self, message: str):
        """Envoie un message sur le canal Telegram"""
        bot = Bot(token=BOT_TOKEN)
        try:
            if len(message) > 4000:
                for i in range(0, len(message), 4000):
                    chunk = message[i:i + 4000]
                    await bot.send_message(chat_id=DEST_CHANNEL, text=chunk)
                    await asyncio.sleep(0.5)
            else:
                await bot.send_message(chat_id=DEST_CHANNEL, text=message)
        except Exception as e:
            print(f"❌ Erreur Telegram: {e}")

    # ==========================================
    # SURVEILLANCE & ALERTES
    # ==========================================

    async def check_new_trades(self):
        """Vérifie les nouveaux trades des wallets"""
        for address, name in self.wallets.items():
            try:
                activities = self.get_wallet_activity(address, limit=5)

                for act in activities:
                    if not isinstance(act, dict):
                        continue
                    if act.get('type') != 'TRADE':
                        continue

                    tx_hash = act.get('transactionHash', '')
                    if not tx_hash or tx_hash in self.seen_txs:
                        continue

                    self.seen_txs.add(tx_hash)

                    side = act.get('side', 'N/A')
                    title = act.get('title', 'Unknown')
                    usdc = float(act.get('usdcSize', 0) or 0)
                    price = float(act.get('price', 0) or 0)
                    outcome = act.get('outcome', '')
                    emoji = "🟢" if side == "BUY" else "🔴"

                    msg = (
                        f"{emoji} {side} - {name}\n\n"
                        f"📊 {title}\n"
                        f"🎯 Outcome: {outcome}\n"
                        f"💰 ${usdc:,.2f} @ {price:.2f}\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S')}"
                    )
                    await self.send_telegram(msg)
                    print(f"{emoji} {name}: {side} ${usdc:.0f} on {title[:40]}...")

            except Exception as e:
                print(f"  Check error {address[:8]}: {e}")

        # Nettoyer les vieux tx hashes
        if len(self.seen_txs) > 2000:
            self.seen_txs = set(list(self.seen_txs)[-1000:])

    # ==========================================
    # RÉSUMÉ 6H
    # ==========================================

    async def generate_wallet_summary(self) -> str:
        """Génère le résumé des achats classé par marché"""
        market_buys = defaultdict(lambda: {
            "wallets": set(), "wallet_names": [],
            "buy_count": 0, "sell_count": 0,
            "total_usdc": 0.0, "prices": [],
            "outcomes": [], "trader_names": set(),
        })

        total_trades = 0

        for address, name in self.wallets.items():
            activities = self.get_wallet_activity(address, limit=50)

            for act in activities:
                if not isinstance(act, dict) or act.get('type') != 'TRADE':
                    continue

                title = act.get('title', '')
                if not title:
                    continue

                total_trades += 1
                side = act.get('side', '')
                usdc = float(act.get('usdcSize', 0) or 0)
                price = float(act.get('price', 0) or 0)
                outcome = act.get('outcome', '')

                market_buys[title]["wallets"].add(address)
                market_buys[title]["wallet_names"].append(name)
                market_buys[title]["total_usdc"] += usdc
                market_buys[title]["trader_names"].add(name)

                if price > 0:
                    market_buys[title]["prices"].append(price)
                if outcome:
                    market_buys[title]["outcomes"].append(outcome)

                if side.upper() == 'BUY':
                    market_buys[title]["buy_count"] += 1
                elif side.upper() == 'SELL':
                    market_buys[title]["sell_count"] += 1

            await asyncio.sleep(0.3)

        if not market_buys:
            return ""

        # Trier par BUY décroissant
        sorted_markets = sorted(
            market_buys.items(),
            key=lambda x: x[1]["buy_count"],
            reverse=True
        )

        # Construire le message
        lines = [
            f"📊 RÉSUMÉ ACHATS WALLETS",
            f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}",
            f"👛 {len(self.wallets)} wallets | {total_trades} trades",
            f"{'='*30}\n",
        ]

        for i, (market, data) in enumerate(sorted_markets[:15], 1):
            avg_price = sum(data["prices"]) / len(data["prices"]) if data["prices"] else 0
            wallet_count = len(data["wallets"])

            if data["buy_count"] >= 5:
                fire = "🔥🔥🔥 "
            elif data["buy_count"] >= 3:
                fire = "🔥🔥 "
            elif data["buy_count"] >= 2:
                fire = "🔥 "
            else:
                fire = ""

            top_outcome = ""
            if data["outcomes"]:
                top = Counter(data["outcomes"]).most_common(1)[0]
                top_outcome = f" → {top[0]}"

            traders = ", ".join(data["trader_names"])

            lines.append(f"{i}. {market[:55]}")
            lines.append(
                f"   {fire}BUY: {data['buy_count']} | SELL: {data['sell_count']} | "
                f"Wallets: {wallet_count}"
            )
            lines.append(f"   Vol: ${data['total_usdc']:,.0f} | Prix moy: {avg_price:.2f}{top_outcome}")
            lines.append(f"   👛 {traders}")
            lines.append("")

        return "\n".join(lines)

    # ==========================================
    # BOUCLE PRINCIPALE
    # ==========================================

    async def periodic_check(self, context: ContextTypes.DEFAULT_TYPE):
        """Appelé toutes les CHECK_INTERVAL secondes"""
        try:
            await self.check_new_trades()
        except Exception as e:
            print(f"❌ Periodic check error: {e}")

    async def periodic_summary(self, context: ContextTypes.DEFAULT_TYPE):
        """Appelé toutes les 6 heures"""
        print(f"📊 Génération du résumé {SUMMARY_INTERVAL_HOURS}h...")
        try:
            summary = await self.generate_wallet_summary()
            if summary:
                await self.send_telegram(summary)
                print("✅ Résumé envoyé!")
            else:
                print("⚠️ Aucune donnée pour le résumé")
        except Exception as e:
            print(f"❌ Summary error: {e}")

    async def run(self):
        """Démarre le bot et la surveillance"""
        print("="*50)
        print("POLYMARKET WALLET TRACKER")
        print("="*50)
        print(f"📤 Canal: {DEST_CHANNEL}")
        print(f"👛 Wallets: {len(self.wallets)}")
        print(f"⏱️ Check: toutes les {CHECK_INTERVAL}s")
        print(f"📊 Résumé: toutes les {SUMMARY_INTERVAL_HOURS}h")
        print("="*50)

        # Créer l'application Telegram
        self.app = Application.builder().token(BOT_TOKEN).build()

        # Ajouter les commandes
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("help", self.cmd_help))
        self.app.add_handler(CommandHandler("add", self.cmd_add))
        self.app.add_handler(CommandHandler("remove", self.cmd_remove))
        self.app.add_handler(CommandHandler("list", self.cmd_list))
        self.app.add_handler(CommandHandler("summary", self.cmd_summary))

        # Résumé automatique toutes les 6h (pas d'alertes individuelles)
        job_queue = self.app.job_queue
        job_queue.run_repeating(
            self.periodic_summary,
            interval=SUMMARY_INTERVAL_HOURS * 3600,
            first=60
        )

        # Envoyer message de démarrage
        await self.send_telegram(
            f"🚀 Bot Polymarket démarré!\n"
            f"👛 {len(self.wallets)} wallets surveillés\n"
            f"📊 Résumé auto toutes les {SUMMARY_INTERVAL_HOURS}h\n\n"
            f"Commandes: /add /remove /list /summary /help"
        )

        print("\n✅ Bot démarré! En attente de commandes et trades...")
        print("Tapez Ctrl+C pour arrêter\n")

        # Démarrer le bot
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()

        # Garder le bot en vie
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, SystemExit):
            print("\n⏹️ Arrêt du bot...")
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()


async def main():
    tracker = PolymarketTracker()
    await tracker.run()


if __name__ == "__main__":
    print("Démarrage du Polymarket Tracker...")
    start_health_server()
    asyncio.run(main())

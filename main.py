#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
FACTUROS MOBILE - Application mobile complète
Version avec TOUTES les tables de l'application principale
"""

# Imports Kivy
from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.scrollview import ScrollView
from kivy.uix.screenmanager import ScreenManager, Screen
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.widget import Widget
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleview.views import RecycleDataViewBehavior
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.properties import StringProperty, NumericProperty
from kivy.clock import Clock
from kivy.metrics import dp
from kivy.core.window import Window
from kivy.utils import platform
from kivy.graphics import Color, RoundedRectangle

# Autres imports
import json
import socket
import threading
import time
from datetime import datetime, timedelta
import sqlite3
import os
import hashlib
import uuid
import random
import re
import webbrowser
import subprocess
import platform as platform_module

from fpdf import FPDF
import tempfile
import urllib.parse
from datetime import datetime
from kivy.graphics import Color, Rectangle

from kivy_garden.graph import Graph, MeshLinePlot, BarPlot, LinePlot
import matplotlib
matplotlib.use('Agg')  # Pour éviter les problèmes de thread
import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
import io
from kivy.core.image import Image as CoreImage
from kivy.graphics.texture import Texture

# Configuration
SYNC_INTERVAL = 30  # secondes
VERSION = "1.0.0"


# ============================================================================
# CLASSES RÉSEAU POUR MOBILE
# ============================================================================

class MobileNetworkManager:
    """Gestionnaire réseau pour l'application mobile"""
    
    def __init__(self, app):
        self.app = app
        self.connected = False
        self.authenticated = False
        self.current_user = None
        self.server_host = None
        self.server_port = 65432
        self.client_uuid = str(uuid.uuid4())
        self.socket = None
        self.sync_thread = None
        self.running = False
        self.auth_response_queue = None
        
    def authenticate(self, username, password):
        """Authentifie l'utilisateur auprès du serveur"""
        if not self.connected:
            print("❌ Non connecté au serveur")
            return False
        
        import queue
        import hashlib
        
        hashed_password = hashlib.sha256(password.encode('utf-8')).hexdigest()
        
        print(f"🔐 Tentative auth: {username}")
        
        auth_msg = {
            'type': 'authenticate',
            'username': username,
            'password': hashed_password,
            'uuid': self.client_uuid,
            'timestamp': datetime.now().isoformat()
        }
        
        self.auth_response_queue = queue.Queue()
        
        if self._send_message(auth_msg):
            try:
                # Attendre la réponse (timeout 10 secondes)
                response = self.auth_response_queue.get(timeout=10)
                if response.get('success'):
                    self.authenticated = True
                    self.current_user = response.get('user')
                    print(f"✅ Authentifié en tant que {username}")
                    return True
                else:
                    print(f"❌ Auth échouée: {response.get('message')}")
                    return False
            except queue.Empty:
                print("⚠️ Timeout authentification - vérifiez le serveur")
                # Ne pas échouer immédiatement, vérifier si on a des données
                if hasattr(self, 'app') and self.app.db:
                    try:
                        conn = self.app.db.get_connection()
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM produits")
                        count = cursor.fetchone()[0]
                        conn.close()
                        
                        if count > 0:
                            print(f"✅ Données déjà présentes ({count} produits) - authentification acceptée")
                            self.authenticated = True
                            return True
                    except:
                        pass
                return False
            finally:
                if hasattr(self, 'auth_response_queue'):
                    delattr(self, 'auth_response_queue')
        return False
    
    def connect_to_server(self, host, port=65432):
        """Connexion au serveur"""
        try:
            print(f"🔗 Mobile: Tentative de connexion à {host}:{port}")
            
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.settimeout(5)
            self.socket.connect((host, port))
            print(f"   Socket connecté!")
            
            if hasattr(self.app, 'ensure_database_exists'):
                self.app.ensure_database_exists()
            
            handshake = {
                'type': 'handshake',
                'app': 'Facturos Mobile',
                'uuid': self.client_uuid,
                'version': '1.0',
                'timestamp': datetime.now().isoformat()
            }
            
            if self._send_message(handshake):
                print("   Handshake envoyé: True")
            else:
                print("   Handshake envoyé: False")
                return False
            
            self.connected = True
            self.server_host = host
            self.server_port = port
            
            self.running = True
            self.receive_thread = threading.Thread(target=self._receive_loop, daemon=True)
            self.receive_thread.start()
            
            print(f"✅ Mobile connecté au serveur {host}:{port}")
            
            # ⭐ NOTIFIER L'APPLICATION DE LA CONNEXION RÉUSSIE
            if hasattr(self.app, 'on_network_connected'):
                self.app.on_network_connected()
            
            return True
            
        except Exception as e:
            print(f"❌ Mobile: Erreur connexion: {e}")
            import traceback
            traceback.print_exc()
            return False
            
    def disconnect(self):
        """Se déconnecte du serveur"""
        self.running = False
        self.connected = False
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        print("🔌 Mobile déconnecté")
    
    def _send_message(self, data):
        """Envoie un message"""
        try:
            if not self.socket:
                print("❌ Socket non connecté")
                return False
                
            message_json = json.dumps(data, ensure_ascii=False)
            message_with_delimiter = message_json + '\n'
            message_bytes = message_with_delimiter.encode('utf-8')
            
            self.socket.sendall(message_bytes)
            print(f"📤 Message envoyé: {data.get('type', 'unknown')}")
            return True
            
        except Exception as e:
            print(f"❌ Erreur envoi: {e}")
            return False

    def _receive_loop(self):
        """Boucle de réception des messages"""
        buffer = ""
        message_count = 0
        
        print("📡 Thread de réception démarré")
        
        while self.running and self.socket:
            try:
                print(f"   Attente de données... (socket: {self.socket})")
                data = self.socket.recv(4096)
                print(f"   Données reçues: {len(data)} bytes")
                
                if not data:
                    print("   Aucune donnée, déconnexion")
                    break
                    
                chunk = data.decode('utf-8')
                print(f"   Chunk: {chunk[:200]}...")
                buffer += chunk
                
                while '\n' in buffer:
                    message, buffer = buffer.split('\n', 1)
                    if message.strip():
                        message_count += 1
                        print(f"📥 Message #{message_count} reçu, taille: {len(message)}")
                        try:
                            parsed = json.loads(message)
                            print(f"   Message parsé: type={parsed.get('type')}")
                            self._process_message(parsed)
                        except json.JSONDecodeError as e:
                            print(f"⚠️ Erreur JSON: {e}")
                            print(f"   Message incriminé: {message[:200]}")
                            continue
                            
            except socket.timeout:
                print("   Timeout, continue...")
                continue
            except Exception as e:
                print(f"❌ Mobile: Erreur réception: {e}")
                import traceback
                traceback.print_exc()
                break
        
        self.connected = False
        print(f"🔌 Mobile: Déconnecté du serveur. {message_count} messages traités.")
    
    def _process_message(self, data):
        """Traite les messages reçus du serveur"""
        try:
            msg_type = data.get('type')
            
            # ⭐ DEBUG - Afficher le message complet
            print(f"📥 Message reçu - Type: {msg_type}")
            if msg_type == 'server_update':
                print(f"   Table: {data.get('table')}")
                print(f"   Action: {data.get('action')}")
                print(f"   Data: {data.get('data')}")            
            
            if msg_type == "auth_response":
                if status == "success":
                    # Continuer avec la synchronisation
                    print("✅ Authentification OK")
                else:
                    # Afficher l'erreur
                    print("❌ Identifiants incorrects")
                    
            elif msg_type == 'sync_data':
                print("📥 Mobile: Données de synchronisation reçues")
                self.app.sync_data_received(data)
                
            elif msg_type == 'handshake_ack':
                print("✅ Mobile: Handshake confirmé")
                
            elif msg_type == 'server_update':
                print("🔄 Mobile: Mise à jour reçue du serveur")
                self.app.apply_server_update(data)
                
            elif msg_type == 'stock_update':
                print(f"📢 Mobile: Mise à jour de stock reçue")
                self.app.apply_stock_update(data)
                
            elif msg_type == 'pong':
                print("🏓 Pong reçu")
                
        except Exception as e:
            print(f"❌ Mobile: Erreur traitement message: {e}")
    
    def request_sync(self):
        """Demande la synchronisation au serveur"""
        if self.connected:
            msg = {
                'type': 'request_sync',
                'uuid': self.client_uuid,
                'timestamp': datetime.now().isoformat()
            }
            return self._send_message(msg)
        return False
    
    def send_update(self, table, action, data):
        """Envoie une mise à jour au serveur"""
        if not self.connected:
            print(f"❌ Mobile: Non connecté au serveur")
            return False
        
        try:
            msg = {
                'type': 'client_update',
                'table': table,
                'action': action,
                'data': data,  # Vérifiez que c'est bien le dictionnaire original
                'uuid': self.client_uuid,
                'timestamp': datetime.now().isoformat()
            }
            
            # ⭐ AJOUTER CE DEBUG
            print(f"\n📤 AVANT ENVOI - data dans send_update:")
            print(f"   data: {data}")
            print(f"   data keys: {list(data.keys())}")
            
            return self._send_message(msg)
            
            if result:
                print(f"   ✅ Message envoyé avec succès")
            else:
                print(f"   ❌ Échec envoi message")
                
            return result
            
        except Exception as e:
            print(f"❌ Mobile: Erreur send_update: {e}")
            import traceback
            traceback.print_exc()
            return False
            
            
    def request_full_sync(self):
        """Demande une synchronisation complète au serveur"""
        if not self.connected:
            print("⚠️ Pas de connexion pour demander la synchronisation")
            return False
        
        sync_msg = {
            'type': 'request_sync',
            'timestamp': datetime.now().isoformat()
        }
        
        print("📤 Demande de synchronisation complète")
        return self._send_message(sync_msg)
    
    def send_ping(self):
        """Envoie un ping pour vérifier la connexion"""
        try:
            ping_msg = {
                'type': 'ping',
                'timestamp': datetime.now().isoformat()
            }
            return self._send_message(ping_msg)
        except Exception as e:
            print(f"❌ Erreur ping: {e}")
            return False            


# ============================================================================
# BASE DE DONNÉES MOBILE - TOUTES LES TABLES
# ============================================================================

class MobileDatabase:
    """Gestionnaire de base de données locale avec TOUTES les tables"""
    
    def __init__(self):
        self.db_path = 'facturos_mobile.db'
        self.init_database()
    
    def get_connection(self):
        """Crée une nouvelle connexion pour le thread courant avec timeout"""
        import sqlite3
        conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def add_log(self, utilisateur_nom, action, module, details=""):
        """Ajoute un log d'activité"""
        import threading
        from datetime import datetime
        
        def _add_log():
            try:
                conn = self.get_connection()
                cursor = conn.cursor()
                now = datetime.now().isoformat()
                
                # ⭐ Vérifier si la table existe, sinon la créer
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='logs_activite'")
                if not cursor.fetchone():
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS logs_activite (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            utilisateur_nom TEXT,
                            action TEXT,
                            module TEXT,
                            date_action TEXT,
                            details TEXT,
                            last_sync TEXT
                        )
                    ''')
                    conn.commit()
                
                # Insérer le log
                cursor.execute("""
                    INSERT INTO logs_activite 
                    (utilisateur_nom, action, module, date_action, details)
                    VALUES (?, ?, ?, ?, ?)
                """, (utilisateur_nom, action, module, now, details[:200]))
                
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"⚠️ Erreur insertion log: {e}")
        
        threading.Thread(target=_add_log, daemon=True).start()
        return True

    def init_entreprise_params(self):
        """Initialise les paramètres entreprise s'ils n'existent pas"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Vérifier si la table existe
            cursor.execute("SELECT COUNT(*) FROM parametres_entreprise")
            count = cursor.fetchone()[0]
            
            if count == 0:
                print("📝 Initialisation des paramètres entreprise par défaut...")
                cursor.execute("""
                    INSERT INTO parametres_entreprise 
                    (nom, adresse, telephone, email, nif, registre_commerce, securite_sociale, slogan)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, ("FACTUROS", "", "", "", "", "", "", "Merci de votre visite"))
                conn.commit()
                print("✅ Paramètres entreprise initialisés")
            
            conn.close()
            return True
            
        except Exception as e:
            print(f"❌ Erreur initialisation entreprise: {e}")
            return False        
        
    
    def init_database(self):
        """Initialise la base de données avec TOUTES les tables"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # ========== TABLE CATEGORIES ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY,
                    nom TEXT NOT NULL,
                    description TEXT,
                    date_creation TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE CLIENTS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY,
                    nom TEXT NOT NULL,
                    email TEXT,
                    telephone TEXT,
                    adresse TEXT,
                    ville TEXT,
                    pays TEXT,
                    type_client TEXT,
                    date_creation TEXT,
                    statut TEXT,
                    notes TEXT,
                    created_at TEXT,
                    uuid TEXT UNIQUE,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE CONFIG_TICKETS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS config_tickets (
                    id INTEGER PRIMARY KEY,
                    nom_etablissement TEXT,
                    message_bienvenue TEXT,
                    slogan TEXT,
                    message_remerciement TEXT,
                    message_pied TEXT,
                    tva_defaut REAL,
                    date_maj TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE PRODUITS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS produits (
                    id INTEGER PRIMARY KEY,
                    nom TEXT NOT NULL,
                    barcode TEXT,
                    description TEXT,
                    prix REAL NOT NULL,
                    tva REAL DEFAULT 0.0,
                    categorie_id INTEGER,
                    quantite_stock INTEGER DEFAULT 0,
                    seuil_alerte INTEGER DEFAULT 5,
                    date_creation TEXT,
                    created_at TEXT,
                    uuid TEXT UNIQUE,
                    image_path TEXT,
                    prix_achat REAL DEFAULT 0.0,
                    actif INTEGER DEFAULT 1,
                    categorie TEXT DEFAULT 'Non catégorisé',
                    last_sync TEXT,
                    FOREIGN KEY (categorie_id) REFERENCES categories (id)
                )
            ''')
            
            # ========== TABLE FACTURES ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS factures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero TEXT UNIQUE,
                    client_id INTEGER,
                    date TEXT NOT NULL,
                    total_ht REAL DEFAULT 0,
                    total_tva REAL DEFAULT 0,
                    total_ttc REAL NOT NULL,
                    statut TEXT DEFAULT 'payée',
                    mode_paiement TEXT,
                    uuid TEXT,
                    modification_ligne TEXT,
                    date_creation TEXT,
                    suppression_ligne TEXT,
                    montant_paye REAL DEFAULT 0,
                    reste_a_payer REAL DEFAULT 0,
                    sync_status TEXT DEFAULT 'synced',
                    last_sync TEXT,
                    server_id INTEGER
                )
            ''')
            
            # ========== TABLE LIGNES_FACTURE ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS lignes_facture (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facture_id INTEGER,
                    produit_id INTEGER,
                    quantite INTEGER,
                    prix_unitaire REAL,
                    prix_ht_unitaire REAL DEFAULT 0,
                    taux_tva REAL DEFAULT 0,
                    montant_tva REAL DEFAULT 0,
                    total_ligne REAL,
                    sync_status TEXT DEFAULT 'synced',
                    FOREIGN KEY (facture_id) REFERENCES factures (id),
                    FOREIGN KEY (produit_id) REFERENCES produits (id)
                )
            ''')

            # Pour les bases existantes, ajouter la colonne si elle n'existe pas
            try:
                cursor.execute("ALTER TABLE lignes_facture ADD COLUMN prix_ht_unitaire REAL DEFAULT 0")
                print("✅ Colonne prix_ht_unitaire ajoutée à lignes_facture")
            except sqlite3.OperationalError:
                pass  # La colonne existe déjà
            
            # ========== TABLE FACTURES_MODIFIEES ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS factures_modifiees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facture_id INTEGER,
                    utilisateur_id INTEGER,
                    utilisateur_nom TEXT,
                    champ_modifie TEXT,
                    ancienne_valeur TEXT,
                    nouvelle_valeur TEXT,
                    date_modification TEXT,
                    notes TEXT,
                    modification_ligne TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE FACTURES_SUPPRIMEES ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS factures_supprimees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    facture_id INTEGER,
                    numero TEXT,
                    client_id INTEGER,
                    date TEXT,
                    total_ht REAL,
                    total_tva REAL,
                    total_ttc REAL,
                    statut TEXT,
                    mode_paiement TEXT,
                    supprime_par TEXT,
                    date_suppression TEXT,
                    raison TEXT,
                    donnees_json TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE HISTORIQUE_COMMUNICATIONS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historique_communications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER,
                    date TEXT,
                    type TEXT,
                    statut TEXT,
                    details TEXT,
                    notes TEXT,
                    utilisateur TEXT,
                    last_sync TEXT,
                    FOREIGN KEY (client_id) REFERENCES clients (id)
                )
            ''')
            
            # ========== TABLE HISTORIQUE_IMPRESSIONS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historique_impressions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    numero_document TEXT,
                    type_document TEXT,
                    client TEXT,
                    total REAL,
                    date_impression TEXT,
                    utilisateur TEXT,
                    statut TEXT,
                    details TEXT,
                    fichier_associe TEXT,
                    created_at TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE LOGS_ACTIVITE ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS logs_activite (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    utilisateur_id INTEGER,
                    utilisateur_nom TEXT,
                    action TEXT,
                    module TEXT,
                    date_action TEXT,
                    details TEXT,
                    ip_address TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE MOUVEMENTS_STOCK ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS mouvements_stock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    produit_id INTEGER,
                    type TEXT,
                    quantite INTEGER,
                    date TEXT,
                    reference TEXT,
                    notes TEXT,
                    utilisateur TEXT,
                    ancien_stock INTEGER,
                    nouveau_stock INTEGER,
                    last_sync TEXT,
                    FOREIGN KEY (produit_id) REFERENCES produits (id)
                )
            ''')
            
            # ========== TABLE PARAMETRES_ENTREPRISE ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS parametres_entreprise (
                    id INTEGER PRIMARY KEY,
                    nom TEXT,
                    adresse TEXT,
                    telephone TEXT,
                    email TEXT,
                    numero_fiscal TEXT,
                    devise TEXT DEFAULT 'FBu',
                    tva_defaut TEXT,
                    langue TEXT,
                    format_date TEXT,
                    alerte_stock TEXT,
                    stock_min TEXT,
                    code_auto TEXT,
                    slogan TEXT,
                    ticket_entete TEXT,
                    tva_reduite TEXT,
                    site_web TEXT,
                    ticket_accueil TEXT,
                    ticket_pied TEXT,
                    ticket_message_fin TEXT,
                    stock_minimum TEXT,
                    tva_standard TEXT,
                    email_gmail TEXT,
                    gmail_app_password TEXT,
                    logo_path TEXT,
                    nif TEXT,
                    registre_commerce TEXT,
                    securite_sociale TEXT,
                    email_destinataire TEXT,
                    email_smtp TEXT,
                    email_port TEXT,
                    email_from TEXT,
                    email_password TEXT,
                    email_seuil TEXT,
                    email_active TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE PRODUITS_MODIFIES ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS produits_modifies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    produit_id INTEGER,
                    champ_modifie TEXT,
                    ancienne_valeur TEXT,
                    nouvelle_valeur TEXT,
                    date_modification TEXT,
                    modifie_par TEXT,
                    notes TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE PRODUITS_SUPPRIMES ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS produits_supprimes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    produit_id INTEGER,
                    nom TEXT,
                    barcode TEXT,
                    description TEXT,
                    prix REAL,
                    tva REAL,
                    categorie_id INTEGER,
                    quantite_stock INTEGER,
                    seuil_alerte INTEGER,
                    date_creation TEXT,
                    date_suppression TEXT,
                    supprime_par TEXT,
                    raison TEXT,
                    image_path TEXT,
                    prix_achat REAL,
                    fournisseur_id INTEGER,
                    unite_mesure TEXT,
                    marge REAL,
                    code_fournisseur TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== TABLE RAPPELS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS rappels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_id INTEGER,
                    date_rappel TEXT,
                    type TEXT,
                    priorite TEXT,
                    notes TEXT,
                    statut TEXT,
                    cree_par TEXT,
                    cree_le TEXT,
                    last_sync TEXT,
                    FOREIGN KEY (client_id) REFERENCES clients (id)
                )
            ''')
            
            # ========== TABLE USERS ==========
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    username TEXT UNIQUE,
                    password TEXT,
                    role TEXT,
                    full_name TEXT,
                    email TEXT,
                    is_active INTEGER,
                    created_at TEXT,
                    last_login TEXT,
                    permissions TEXT,
                    uuid TEXT,
                    last_sync TEXT
                )
            ''')
            
            # ========== CRÉER LES INDEX ==========
            try:
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_mouvements_date ON mouvements_stock(date)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_mouvements_produit ON mouvements_stock(produit_id)')
                cursor.execute('CREATE INDEX IF NOT EXISTS idx_mouvements_type ON mouvements_stock(type)')
                cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_uuid ON users(uuid)')
            except Exception as e:
                print(f"⚠️ Erreur création index: {e}")
            
            conn.commit()
            print("✅ Base mobile initialisée avec TOUTES les tables")
            
        except Exception as e:
            print(f"❌ Erreur base mobile: {e}")
            import traceback
            traceback.print_exc()
        finally:
            conn.close()
    
    # ========== MÉTHODES DE SYNCHRONISATION ==========
    
    def sync_from_server(self, server_data):
        """Synchronise les données depuis le serveur - Version adaptée à la structure réelle"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            print("\n" + "="*80)
            print("📦 SYNCHRONISATION MOBILE")
            print("="*80)
            
            # Au début de sync_from_server, après le print
            print("\n🔍 DEBUG: Structure des données reçues du serveur")
            for table_name, rows in server_data.items():
                if table_name == 'produits' and rows:
                    print(f"\n📦 Table {table_name} - {len(rows)} lignes")
                    if rows:
                        print(f"   Première ligne: {rows[0]}")
                        print(f"   Nombre de colonnes: {len(rows[0])}")
                        # Afficher les noms des colonnes (index)
                        for i, value in enumerate(rows[0]):
                            print(f"      Index {i}: {value}")               
            
            total_inserted = 0
            total_updated = 0
            
            # =========================================================
            # 1. CLIENTS - Structure: [id, nom, email, telephone, adresse, ville, pays]
            # =========================================================
            if 'clients' in server_data and server_data['clients']:
                clients = server_data['clients']
                print(f"\n👥 Synchronisation de {len(clients)} clients...")
                inserted = 0
                updated = 0
                
                for client in clients:
                    try:
                        if len(client) >= 2:
                            client_id = client[0]
                            nom = client[1] if len(client) > 1 else 'Client sans nom'
                            email = client[2] if len(client) > 2 else ''
                            telephone = client[3] if len(client) > 3 else ''
                            adresse = client[4] if len(client) > 4 else ''
                            ville = client[5] if len(client) > 5 else ''
                            pays = client[6] if len(client) > 6 else ''
                            
                            # Vérifier si le client existe déjà
                            cursor.execute("SELECT id FROM clients WHERE id = ?", (client_id,))
                            existing = cursor.fetchone()
                            
                            if existing:
                                # MISE À JOUR
                                cursor.execute('''
                                    UPDATE clients SET 
                                        nom = ?, email = ?, telephone = ?, adresse = ?, ville = ?, pays = ?
                                    WHERE id = ?
                                ''', (nom, email, telephone, adresse, ville, pays, client_id))
                                updated += 1
                                print(f"   🔄 Client {nom} mis à jour")
                            else:
                                # NOUVEAU
                                cursor.execute('''
                                    INSERT INTO clients (id, nom, email, telephone, adresse, ville, pays, statut)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (client_id, nom, email, telephone, adresse, ville, pays, 'actif'))
                                inserted += 1
                                print(f"   ✅ Nouveau client {nom}")
                                
                    except Exception as e:
                        print(f"   ❌ Erreur client: {e}")
                
                total_inserted += inserted
                total_updated += updated
                print(f"✅ Clients: {updated} mis à jour, {inserted} nouveaux")
            
            # =========================================================
            # 2. PRODUITS - Structure: [id, nom, prix, quantite_stock, seuil_alerte, tva, description, barcode]
            # =========================================================
            if 'produits' in server_data and server_data['produits']:
                produits = server_data['produits']
                print(f"\n📦 Synchronisation de {len(produits)} produits...")
                
                for prod in produits:
                    try:
                        if len(prod) >= 9:  # 9 colonnes avec categorie
                            prod_id = prod[0]
                            nom = prod[1] if len(prod) > 1 else 'Produit sans nom'
                            prix = float(prod[2]) if len(prod) > 2 else 0
                            quantite_stock = int(prod[3]) if len(prod) > 3 else 0
                            seuil_alerte = int(prod[4]) if len(prod) > 4 else 5
                            tva = float(prod[5]) if len(prod) > 5 else 0
                            description = prod[6] if len(prod) > 6 else ''
                            barcode = prod[7] if len(prod) > 7 else ''
                            
                            # ⭐ RÉCUPÉRER LA CATÉGORIE DIRECTEMENT (index 8)
                            categorie_nom = prod[8] if len(prod) > 8 and prod[8] else 'Non catégorisé'
                            
                            print(f"   📌 Produit: {nom} -> Catégorie: {categorie_nom}")
                            
                            cursor.execute("SELECT id FROM produits WHERE id = ?", (prod_id,))
                            existing = cursor.fetchone()
                            
                            if existing:
                                cursor.execute('''
                                    UPDATE produits SET 
                                        nom = ?, prix = ?, quantite_stock = ?, seuil_alerte = ?,
                                        tva = ?, description = ?, barcode = ?, categorie = ?
                                    WHERE id = ?
                                ''', (nom, prix, quantite_stock, seuil_alerte, tva, description, barcode, categorie_nom, prod_id))
                                print(f"   🔄 Produit {nom} mis à jour (catégorie: {categorie_nom})")
                            else:
                                cursor.execute('''
                                    INSERT INTO produits 
                                    (id, nom, prix, quantite_stock, seuil_alerte, tva, description, barcode, categorie, actif)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (prod_id, nom, prix, quantite_stock, seuil_alerte, tva, description, barcode, categorie_nom, 1))
                                print(f"   ✅ Nouveau produit {nom} (catégorie: {categorie_nom})")
                                
                    except Exception as e:
                        print(f"   ❌ Erreur produit: {e}")
            
            # =========================================================
            # 3. FACTURES - Structure: [id, numero, client_id, date, total_ht, total_tva, total_ttc, statut, mode_paiement, uuid]
            # =========================================================
            if 'factures' in server_data and server_data['factures']:
                factures = server_data['factures']
                print(f"\n🧾 Synchronisation de {len(factures)} factures...")
                
                for fact in factures:
                    try:
                        # ⭐ Afficher la structure reçue
                        print(f"   Facture reçue: {fact}")
                        
                        # [id, numero, client_id, date, total_ht, total_tva, total_ttc, statut, mode_paiement, uuid]
                        if len(fact) >= 10:
                            fact_id = fact[0]
                            numero = fact[1]
                            client_id = fact[2]
                            date_fact = fact[3]
                            total_ht = float(fact[4]) if fact[4] else 0
                            total_tva = float(fact[5]) if fact[5] else 0
                            total_ttc = float(fact[6]) if fact[6] else 0
                            statut = fact[7] if fact[7] else 'payée'
                            mode_paiement = fact[8] if fact[8] else 'Espèces'
                            fact_uuid = fact[9] if fact[9] else str(uuid.uuid4())
                            
                            print(f"   📝 {numero} - Statut: {statut} - Client: {client_id}")
                            
                            # Vérifier si la facture existe déjà
                            cursor.execute("SELECT id FROM factures WHERE numero = ?", (numero,))
                            existing = cursor.fetchone()
                            
                            if existing:
                                # Mise à jour
                                cursor.execute('''
                                    UPDATE factures SET 
                                        date = ?, client_id = ?, total_ht = ?, total_tva = ?, total_ttc = ?,
                                        statut = ?, mode_paiement = ?, uuid = ?
                                    WHERE numero = ?
                                ''', (date_fact, client_id, total_ht, total_tva, total_ttc, 
                                      statut, mode_paiement, fact_uuid, numero))
                                print(f"   🔄 Facture {numero} mise à jour")
                            else:
                                # Nouvelle facture
                                cursor.execute('''
                                    INSERT INTO factures 
                                    (numero, date, client_id, total_ht, total_tva, total_ttc, 
                                     statut, mode_paiement, uuid, sync_status)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (numero, date_fact, client_id, total_ht, total_tva, total_ttc, 
                                      statut, mode_paiement, fact_uuid, 'synced'))
                                print(f"   ✅ Nouvelle facture {numero} insérée")
                                
                    except Exception as e:
                        print(f"   ❌ Erreur facture: {e}")
                        import traceback
                        traceback.print_exc()

            # ⭐⭐⭐ AJOUTER CETTE PARTIE : SYNCHRONISATION DES LIGNES DE FACTURE ⭐⭐⭐
            if 'lignes_facture' in server_data and server_data['lignes_facture']:
                lignes = server_data['lignes_facture']
                print(f"\n📄 Synchronisation de {len(lignes)} lignes de facture...")
                
                for ligne in lignes:
                    try:
                        # Structure: [id, facture_id, produit_id, quantite, prix_unitaire, taux_tva, montant_tva, total_ligne]
                        if len(ligne) >= 8:
                            ligne_id = ligne[0]
                            facture_id = ligne[1]
                            produit_id = ligne[2]
                            quantite = ligne[3] if len(ligne) > 3 else 1
                            prix_unitaire = float(ligne[4]) if len(ligne) > 4 else 0
                            taux_tva = float(ligne[5]) if len(ligne) > 5 else 0
                            montant_tva = float(ligne[6]) if len(ligne) > 6 else 0
                            total_ligne = float(ligne[7]) if len(ligne) > 7 else 0
                            
                            # Vérifier si la facture existe
                            cursor.execute("SELECT id FROM factures WHERE numero = ?", (numero,))
                            facture_exist = cursor.fetchone()
                            
                            if facture_exist:
                                cursor.execute('''
                                    INSERT OR REPLACE INTO lignes_facture 
                                    (id, facture_id, produit_id, quantite, prix_unitaire, 
                                     taux_tva, montant_tva, total_ligne, sync_status)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                                ''', (ligne_id, facture_id, produit_id, quantite, prix_unitaire,
                                      taux_tva, montant_tva, total_ligne, 'synced'))
                                print(f"   ✅ Ligne facture {facture_id} - produit {produit_id} - qte {quantite}")
                            else:
                                print(f"   ⚠️ Facture {facture_id} non trouvée pour ligne {ligne_id}")
                                
                    except Exception as e:
                        print(f"   ❌ Erreur ligne facture: {e}")
                
            # =========================================================
            # 4. LIGNES DE FACTURE - VERSION CORRIGÉE ⭐
            # =========================================================
            if 'lignes_facture' in server_data and server_data['lignes_facture']:
                lignes = server_data['lignes_facture']
                print(f"\n📄 Synchronisation de {len(lignes)} lignes de facture...")
                
                for ligne in lignes:
                    try:
                        # Structure attendue: [id, facture_numero, produit_id, quantite, prix_unitaire, taux_tva, montant_tva, total_ligne]
                        if len(ligne) >= 8:
                            ligne_id = ligne[0]
                            facture_numero = ligne[1]      # ⭐ Utiliser le NUMERO de facture
                            produit_id = ligne[2]
                            quantite = ligne[3] if len(ligne) > 3 else 1
                            prix_unitaire = float(ligne[4]) if len(ligne) > 4 else 0
                            taux_tva = float(ligne[5]) if len(ligne) > 5 else 0
                            montant_tva = float(ligne[6]) if len(ligne) > 6 else 0
                            total_ligne = float(ligne[7]) if len(ligne) > 7 else 0
                            
                            print(f"   Ligne reçue: facture={facture_numero}, produit={produit_id}, qte={quantite}")
                            
                            # ⭐ Chercher la facture par son NUMERO (pas par ID)
                            cursor.execute("SELECT id FROM factures WHERE numero = ?", (facture_numero,))
                            facture_result = cursor.fetchone()
                            
                            if facture_result:
                                facture_id_local = facture_result[0]
                                
                                # Vérifier si la ligne existe déjà
                                cursor.execute("""
                                    SELECT id FROM lignes_facture 
                                    WHERE facture_id = ? AND produit_id = ?
                                """, (facture_id_local, produit_id))
                                existing_ligne = cursor.fetchone()
                                
                                if existing_ligne:
                                    # Mise à jour
                                    cursor.execute('''
                                        UPDATE lignes_facture SET 
                                            quantite = ?, prix_unitaire = ?, taux_tva = ?, 
                                            montant_tva = ?, total_ligne = ?, sync_status = ?
                                        WHERE facture_id = ? AND produit_id = ?
                                    ''', (quantite, prix_unitaire, taux_tva, montant_tva, total_ligne, 
                                          'synced', facture_id_local, produit_id))
                                    print(f"   🔄 Ligne facture {facture_numero} - produit {produit_id} mise à jour")
                                else:
                                    # Nouvelle insertion
                                    cursor.execute('''
                                        INSERT INTO lignes_facture 
                                        (facture_id, produit_id, quantite, prix_unitaire, 
                                         taux_tva, montant_tva, total_ligne, sync_status)
                                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                    ''', (facture_id_local, produit_id, quantite, prix_unitaire,
                                          taux_tva, montant_tva, total_ligne, 'synced'))
                                    print(f"   ✅ Nouvelle ligne facture {facture_numero} - produit {produit_id}")
                            else:
                                print(f"   ⚠️ Facture {facture_numero} non trouvée pour la ligne {ligne_id}")
                                
                    except Exception as e:
                        print(f"   ❌ Erreur ligne facture: {e}")
                        import traceback
                        traceback.print_exc()
                
            # =========================================================
            # 5. CATÉGORIES - Structure: [id, nom]
            # =========================================================
            # ========== CATÉGORIES - AVEC INSERT OR REPLACE ==========
            if 'categories' in server_data and server_data['categories']:
                categories = server_data['categories']
                print(f"\n📂 Synchronisation de {len(categories)} catégories...")
                for cat in categories:
                    try:
                        if len(cat) >= 2:
                            cat_id = cat[0]
                            nom = cat[1] if len(cat) > 1 else 'Catégorie'
                            description = cat[2] if len(cat) > 2 else ''
                            date_creation = cat[3] if len(cat) > 3 else datetime.now().isoformat()
                            
                            cursor.execute('''
                                INSERT OR REPLACE INTO categories (id, nom, description, date_creation)
                                VALUES (?, ?, ?, ?)
                            ''', (cat_id, nom, description, date_creation))
                            print(f"   ✅ Catégorie: {nom} (ID: {cat_id})")
                            total_inserted += 1
                    except Exception as e:
                        print(f"   ❌ Erreur catégorie: {e}")
                print(f"✅ Catégories synchronisées")
            
            conn.commit()
            
            print("\n" + "="*80)
            print("📊 RÉCAPITULATIF DE LA SYNCHRONISATION")
            print("="*80)
            print(f"   ✅ Mises à jour: {total_updated}")
            print(f"   ✅ Nouvelles insertions: {total_inserted}")
            print("="*80)
            
            return True
            
        except Exception as e:
            print(f"❌ Erreur sync: {e}")
            import traceback
            traceback.print_exc()
            conn.rollback()
            return False
        finally:
            conn.close()
            
    def add_communication(self, client_id, comm_type, details, notes=""):
        """Ajoute une communication dans l'historique"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute('''
                INSERT INTO historique_communications 
                (client_id, date, type, statut, details, notes, utilisateur)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (client_id, now, comm_type, 'envoyé', details, notes, 'Mobile'))
            conn.commit()
            return True
        except Exception as e:
            print(f"❌ Erreur add_communication: {e}")
            return False
        finally:
            conn.close()   
   
    # ========== MÉTHODES DE LECTURE ==========
    
    def determiner_categorie(self, nom, description):
        """Détermine la catégorie à partir du nom et de la description"""
        nom_lower = nom.lower()
        desc_lower = description.lower() if description else ''
        
        # ⭐ ÉLECTRONIQUE / INFORMATIQUE (priorité)
        mots_electronique = ['laptop', 'ordinateur', 'pc', 'dell', 'hp', 'lenovo', 'acer', 'asus', 
                             'toshiba', 'samsung', 'apple', 'macbook', 'imac', 'ecran', 'moniteur',
                             'clavier', 'souris', 'imprimante', 'scaner', 'disque', 'ssd', 'ram']
        if any(word in nom_lower for word in mots_electronique):
            return 'Électronique'
        if any(word in desc_lower for word in mots_electronique):
            return 'Électronique'
        
        # ⭐ Alimentation
        mots_alimentation = ['cafe', 'oeuf', 'pain', 'lait', 'eau', 'jus', 'riz', 'farine', 
                             'banane', 'pomme', 'orange', 'mangue', 'ananas', 'tomate', 'oignon',
                             'sucre', 'sel', 'huile', 'beurre', 'fromage', 'yaourt', 'viande',
                             'poisson', 'poulet', 'boeuf', 'porc', 'legume', 'fruit']
        if any(word in nom_lower for word in mots_alimentation):
            return 'Alimentation'
        if any(word in desc_lower for word in mots_alimentation):
            return 'Alimentation'
        
        # ⭐ BOISSON
        mots_boisson = ['coca', 'fanta', 'sprite', 'pepsi', 'schweppes', 'orangina', 
                        'eau', 'jus', 'biere', 'vin', 'whisky', 'vodka', 'rhum', 
                        'the', 'cafe', 'chocolat', 'lait', 'soda']
        if any(word in nom_lower for word in mots_boisson):
            return 'BOISSON'
        
        # ⭐ Vêtements
        mots_vetements = ['chemise', 'pantalon', 'robe', 'chaussure', 't-shirt', 'jeans', 
                          'jupe', 'veste', 'manteau', 'pull', 'sweat', 'short', 'casquette']
        if any(word in nom_lower for word in mots_vetements):
            return 'Vêtements'
        
        # ⭐ Maison
        mots_maison = ['table', 'chaise', 'lit', 'armoire', 'casserole', 'assiette', 
                       'verre', 'tasse', 'couvert', 'nappe', 'rideau', 'coussin', 'tapis']
        if any(word in nom_lower for word in mots_maison):
            return 'Maison'
        
        # ⭐ Bureau
        mots_bureau = ['stylo', 'cahier', 'papier', 'agrafeuse', 'cartouche', 'chemise',
                       'classeur', 'trombone', 'gomme', 'crayon', 'regle', 'calculatrice']
        if any(word in nom_lower for word in mots_bureau):
            return 'Bureau'
        
        # ⭐ TABAC
        mots_tabac = ['cigarette', 'tabac', 'cigar', 'cigare', 'pipe', 'chicha']
        if any(word in nom_lower for word in mots_tabac):
            return 'TABAC'
        
        return 'Non catégorisé'
    
    
    
    def get_clients(self):
        """Récupère tous les clients"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, nom, email, telephone FROM clients WHERE statut != 'inactif' OR statut IS NULL ORDER BY nom")
            return cursor.fetchall()
        except Exception as e:
            print(f"❌ Erreur get_clients: {e}")
            return []
        finally:
            conn.close()
            
    
    def get_produits(self):
        """Récupère tous les produits avec leurs détails"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # Utiliser directement la colonne categorie qui est déjà correcte
            cursor.execute("""
                SELECT 
                    id, 
                    nom, 
                    prix, 
                    quantite_stock, 
                    seuil_alerte, 
                    tva, 
                    description, 
                    barcode,
                    categorie  -- Utiliser la colonne categorie directement
                FROM produits 
                WHERE actif = 1 
                ORDER BY nom
            """)
            produits = cursor.fetchall()
            
            # Debug: afficher les catégories récupérées
            print(f"\n📋 Produits chargés avec leurs catégories:")
            for p in produits:
                cat = p[8] if len(p) > 8 and p[8] else 'Non catégorisé'
                print(f"   • {p[1]} -> Catégorie: {cat}")
            
            return produits
        except Exception as e:
            print(f"❌ Erreur get_produits: {e}")
            import traceback
            traceback.print_exc()
            return []
        finally:
            conn.close()
            
    
    def get_categories(self):
        """Récupère toutes les catégories"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, nom FROM categories ORDER BY nom")
            return cursor.fetchall()
        except Exception as e:
            print(f"❌ Erreur get_categories: {e}")
            return []
        finally:
            conn.close()
    
    def get_ca_today(self):
        """Récupère le CA du jour"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            print(f"🔍 get_ca_today: recherche CA pour {today}")
            cursor.execute("SELECT COALESCE(SUM(total_ttc), 0) FROM factures WHERE date LIKE ?", (f"{today}%",))
            result = cursor.fetchone()[0]
            print(f"💰 get_ca_today résultat: {result}")
            return result if result else 0
        except Exception as e:
            print(f"❌ Erreur get_ca_today: {e}")
            return 0
        finally:
            conn.close()

    def get_ventes_today(self):
        """Récupère le nombre de ventes du jour"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            print(f"📅 Recherche ventes du {today}")
            
            cursor.execute("SELECT COUNT(*) FROM factures WHERE date LIKE ?", (f"{today}%",))
            result = cursor.fetchone()[0]
            print(f"📊 Ventes aujourd'hui: {result}")
            return result if result else 0
        except Exception as e:
            print(f"❌ Erreur get_ventes_today: {e}")
            return 0
        finally:
            conn.close()

    def get_ca_mois(self):
        """Récupère le CA du mois"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            now = datetime.now()
            # ⭐ Format YYYY-MM pour la recherche
            mois_cible = now.strftime('%Y-%m')
            print(f"📅 Recherche CA du mois {mois_cible}")
            
            cursor.execute("SELECT COALESCE(SUM(total_ttc), 0) FROM factures WHERE date LIKE ?", (f"{mois_cible}%",))
            result = cursor.fetchone()[0]
            
            print(f"📊 CA mois {mois_cible}: {result}")
            return result if result else 0
        except Exception as e:
            print(f"❌ Erreur get_ca_mois: {e}")
            return 0
        finally:
            conn.close()

    def get_ventes_recentes(self, limit=20):
        """Récupère les ventes récentes avec les noms des clients"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            query = """
                SELECT 
                    f.numero,
                    f.date as date_originale,
                    substr(f.date, 1, 10) as date_affichage,  -- Prendre seulement la date sans l'heure
                    f.total_ttc,
                    COALESCE(c.nom, 'Client inconnu') as client_nom
                FROM factures f
                LEFT JOIN clients c ON f.client_id = c.id
                ORDER BY f.id DESC
                LIMIT ?
            """
            
            cursor.execute(query, (limit,))
            ventes = cursor.fetchall()
            
            # Formater les résultats
            ventes_formatees = []
            for v in ventes:
                ventes_formatees.append((
                    v[0],           # numero
                    v[2],           # date_affichage (YYYY-MM-DD)
                    v[3],           # total_ttc
                    v[4],           # client_nom
                    'synced'        # sync_status par défaut
                ))
            
            print(f"💰 Ventes chargées: {len(ventes_formatees)}")
            
            # Afficher les 10 premières pour debug
            if ventes_formatees:
                print("   Échantillon des 10 ventes les plus récentes:")
                for i, v in enumerate(ventes_formatees[:10]):
                    print(f"   • {v[0]} - Date: {v[1]} - Client: {v[3]} - {v[2]:.0f} Fbu")
            
            return ventes_formatees
            
        except Exception as e:
            print(f"❌ Erreur get_ventes_recentes: {e}")
            import traceback
            traceback.print_exc()
            return []
        finally:
            conn.close()

    
    def get_alertes_stock(self):
        """Récupère les alertes de stock"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT nom, quantite_stock, seuil_alerte, prix 
                FROM produits 
                WHERE actif = 1 AND quantite_stock <= seuil_alerte 
                ORDER BY quantite_stock ASC
            """)
            return cursor.fetchall()
        except Exception as e:
            print(f"❌ Erreur get_alertes_stock: {e}")
            return []
        finally:
            conn.close()

    def get_stock_faible(self):
        """Récupère le nombre de produits en stock faible"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM produits WHERE actif = 1 AND quantite_stock <= seuil_alerte AND quantite_stock > 0")
            return cursor.fetchone()[0] or 0
        finally:
            conn.close()
    
    def get_alertes_count(self):
        """Récupère le nombre d'alertes critiques"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM produits WHERE actif = 1 AND quantite_stock <= 0")
            return cursor.fetchone()[0] or 0
        finally:
            conn.close()
    
    def get_total_factures(self):
        """Récupère le nombre total de factures"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT COUNT(*) FROM factures")
            return cursor.fetchone()[0] or 0
        finally:
            conn.close()
    
    def get_pending_sync(self):
        """Récupère les éléments en attente de synchronisation"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            # ⭐ IMPORTANT: Spécifier l'ordre des colonnes
            cursor.execute("""
                SELECT 
                    id, 
                    numero, 
                    client_id, 
                    date, 
                    total_ht, 
                    total_tva, 
                    total_ttc, 
                    statut, 
                    mode_paiement, 
                    montant_paye, 
                    reste_a_payer
                FROM factures 
                WHERE sync_status = 'pending'
            """)
            factures = cursor.fetchall()
            
            # Debug
            if factures:
                print(f"📋 {len(factures)} factures en attente:")
                for f in factures:
                    print(f"   ID:{f[0]}, N°:{f[1]}, Client:{f[2]}")
                    print(f"      HT:{f[4]}, TVA:{f[5]}, TTC:{f[6]}")
                    print(f"      Statut:{f[7]}, Paiement:{f[8]}")
            
            return {'factures': factures}
        except Exception as e:
            print(f"❌ Erreur get_pending_sync: {e}")
            import traceback
            traceback.print_exc()
            return {'factures': []}
        finally:
            conn.close()
              
    def add_facture(self, client_id, total, mode_paiement, lignes, statut="payée", montant_paye=0):
        """Ajoute une facture en local"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            numero = self.generer_numero_facture(cursor)
            now = datetime.now()
            date_formatted = now.strftime("%Y-%m-%d %H:%M:%S")
            
            total_ttc = total
            
            # ⭐⭐⭐ INITIALISER LES VARIABLES AVANT DE LES UTILISER ⭐⭐⭐
            total_ht = 0
            total_tva = 0
            
            print(f"📝 Insertion facture locale:")
            print(f"   numero: {numero}")
            print(f"   client_id: {client_id}")
            print(f"   total_ttc: {total_ttc}")
            print(f"   statut: {statut}")
            print(f"   mode_paiement: {mode_paiement}")
            print(f"   montant_paye: {montant_paye}")
            
            # Stocker les détails des lignes pour insertion
            lignes_details = []
            
            for ligne in lignes:
                produit_id = ligne.get('produit_id')
                quantite = ligne.get('quantite', 1)
                prix_ttc_unitaire = ligne.get('prix', 0)
                
                # Récupérer le taux TVA du produit
                cursor.execute("SELECT tva FROM produits WHERE id = ?", (produit_id,))
                tva_result = cursor.fetchone()
                taux_tva = tva_result[0] if tva_result else 0
                
                # Calculer HT unitaire
                if taux_tva > 0:
                    prix_ht_unitaire = prix_ttc_unitaire / (1 + taux_tva / 100)
                else:
                    prix_ht_unitaire = prix_ttc_unitaire
                
                # Calculer les totaux
                ligne_ht = prix_ht_unitaire * quantite
                ligne_tva = ligne_ht * (taux_tva / 100)
                ligne_ttc = ligne_ht + ligne_tva
                
                total_ht += ligne_ht
                total_tva += ligne_tva
                
                lignes_details.append({
                    'produit_id': produit_id,
                    'quantite': quantite,
                    'prix_ht_unitaire': prix_ht_unitaire,
                    'prix_ttc_unitaire': prix_ttc_unitaire,
                    'taux_tva': taux_tva,
                    'montant_tva': ligne_tva,
                    'total_ligne': ligne_ttc
                })
            
            print(f"   total_ht calculé: {total_ht}")
            print(f"   total_tva calculé: {total_tva}")
            
            # Calculer reste_a_payer
            reste_a_payer = total_ttc - montant_paye
            
            # Insérer la facture
            cursor.execute('''
                INSERT INTO factures 
                (numero, client_id, date, total_ht, total_tva, total_ttc, statut, mode_paiement, montant_paye, reste_a_payer, sync_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (numero, client_id, date_formatted, total_ht, total_tva, total_ttc, statut, mode_paiement, montant_paye, reste_a_payer, 'pending'))
            
            facture_id = cursor.lastrowid
            print(f"✅ Facture locale insérée: ID={facture_id}")
            
            # Insérer les lignes
            for detail in lignes_details:
                cursor.execute('''
                    INSERT INTO lignes_facture 
                    (facture_id, produit_id, quantite, prix_unitaire, prix_ht_unitaire, taux_tva, montant_tva, total_ligne, sync_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    facture_id, 
                    detail['produit_id'], 
                    detail['quantite'], 
                    detail['prix_ttc_unitaire'],
                    detail['prix_ht_unitaire'],
                    detail['taux_tva'], 
                    detail['montant_tva'], 
                    detail['total_ligne'], 
                    'pending'
                ))
                
                # Mettre à jour le stock (seulement si la facture n'est pas annulée)
                if statut != "annulée":
                    cursor.execute('''
                        UPDATE produits SET quantite_stock = quantite_stock - ? WHERE id = ?
                    ''', (detail['quantite'], detail['produit_id']))
            
            conn.commit()
            print(f"✅ Facture {numero} enregistrée avec succès")
            print(f"   Totaux: HT={total_ht:.2f}, TVA={total_tva:.2f}, TTC={total_ttc:.2f}")
            print(f"   Lignes insérées: {len(lignes_details)}")
            
            return facture_id, numero
            
        except Exception as e:
            print(f"❌ Erreur add_facture: {e}")
            import traceback
            traceback.print_exc()
            conn.rollback()
            return None, None
        finally:
            conn.close()

    def generer_numero_facture(self, cursor):
        """Génère un numéro de facture unique - VERSION QUI FONCTIONNE"""
        from datetime import datetime
        
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        numero = f"FACT-{timestamp}"
        
        # Vérifier si ce numéro existe déjà (au cas où deux factures seraient créées la même seconde)
        cursor.execute("SELECT id FROM factures WHERE numero = ?", (numero,))
        existing = cursor.fetchone()
        
        if not existing:
            return numero
        else:
            # En cas de collision (très rare), ajouter un suffixe
            for i in range(1, 100):
                numero_avec_suffixe = f"FACT-{timestamp}-{i:02d}"
                cursor.execute("SELECT id FROM factures WHERE numero = ?", (numero_avec_suffixe,))
                if not cursor.fetchone():
                    return numero_avec_suffixe
        
        # Au cas où (pratiquement impossible d'arriver ici)
        from time import time
        return f"FACT-{int(time())}"
        
        
                        
    def mark_synced(self, table, record_id):
        """Marque un enregistrement comme synchronisé"""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            if table == 'factures':
                cursor.execute('''
                    UPDATE factures SET sync_status = 'synced', last_sync = ? WHERE id = ?
                ''', (datetime.now().isoformat(), record_id))
            conn.commit()
        except Exception as e:
            print(f"❌ Erreur mark_synced: {e}")
        finally:
            conn.close()
            
    def sync_pending_invoices(self):
        """Synchronise les factures en attente avec le serveur"""
        print("🔄 Synchronisation des factures en attente...")
        
        app = App.get_running_app()
        
        if not app.network or not app.network.connected:
            print("⚠️ Pas de connexion réseau - synchronisation différée")
            return False
        
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Récupérer les factures en attente
        cursor.execute("""
            SELECT id, numero, client_id, date, total_ht, total_tva, total_ttc, 
                   statut, mode_paiement, montant_paye, reste_a_payer
            FROM factures 
            WHERE sync_status = 'pending'
            ORDER BY id ASC
        """)
        
        factures_pending = cursor.fetchall()
        
        if not factures_pending:
            print("✅ Aucune facture en attente")
            conn.close()
            return True
        
        print(f"📋 {len(factures_pending)} facture(s) en attente")
        
        success_count = 0
        
        for facture in factures_pending:
            facture_id = facture[0]
            numero = facture[1]
            client_id = facture[2]
            date_fact = facture[3]
            total_ht = facture[4]
            total_tva = facture[5]
            total_ttc = facture[6]
            statut = facture[7]
            mode_paiement = facture[8]
            montant_paye = facture[9]
            reste_a_payer = facture[10]
            
            print(f"\n📤 SYNC PENDING - Envoi facture:")
            print(f"   numero: {numero}")
            print(f"   total_ht: {total_ht}")
            print(f"   total_tva: {total_tva}")
            print(f"   total_ttc: {total_ttc}")
            print(f"   statut: {statut}")
            print(f"   mode_paiement: {mode_paiement}")
            print(f"   montant_paye: {montant_paye}")
            print(f"   reste_a_payer: {reste_a_payer}")
            
            # ⭐⭐⭐ RÉCUPÉRER LES LIGNES DE LA FACTURE ⭐⭐⭐
            cursor.execute("""
                SELECT lf.produit_id, lf.quantite, lf.prix_unitaire, lf.total_ligne, p.tva
                FROM lignes_facture lf
                JOIN produits p ON lf.produit_id = p.id
                WHERE lf.facture_id = ?
            """, (facture_id,))
            
            lignes = cursor.fetchall()
            print(f"   {len(lignes)} ligne(s) trouvée(s)")
            
            # Préparer les lignes pour le serveur
            lignes_serveur = []
            for ligne in lignes:
                produit_id = ligne[0]
                quantite = ligne[1]
                prix_ttc = ligne[2]
                total_ligne = ligne[3]
                taux_tva = ligne[4] or 0
                
                # Calculer le prix HT
                if taux_tva > 0:
                    prix_ht = prix_ttc / (1 + taux_tva / 100)
                    montant_tva = (prix_ht * quantite) * (taux_tva / 100)
                else:
                    prix_ht = prix_ttc
                    montant_tva = 0
                
                lignes_serveur.append({
                    'produit_id': produit_id,
                    'quantite': quantite,
                    'prix_unitaire': prix_ht,
                    'taux_tva': taux_tva,
                    'montant_tva': montant_tva,
                    'total_ligne': total_ligne
                })
                print(f"   Ligne: produit={produit_id}, qte={quantite}, total={total_ligne}")
            
            # ⭐⭐⭐ CONSTRUIRE facture_data AVEC LES LIGNES ⭐⭐⭐
            facture_data = {
                'numero': numero,
                'client_id': client_id,
                'date': date_fact,
                'total_ht': float(total_ht),
                'total_tva': float(total_tva),
                'total_ttc': float(total_ttc),
                'statut': statut,
                'mode_paiement': mode_paiement,
                'montant_paye': float(montant_paye),
                'reste_a_payer': float(reste_a_payer),
                'lignes': lignes_serveur  # ⭐ INCLURE LES LIGNES
            }
            
            print(f"📤 AVANT ENVOI - data keys: {list(facture_data.keys())}")
            print(f"   nombre de lignes: {len(facture_data.get('lignes', []))}")
            
            # Envoyer au serveur
            if app.network and app.network.connected:
                # ⭐ UTILISER send_facture() au lieu de send_update direct
                app.network.send_update('factures', 'insert', facture_data)
                
                # Marquer comme synchronisée
                cursor.execute("""
                    UPDATE factures 
                    SET sync_status = 'synced' 
                    WHERE id = ?
                """, (facture_id,))
                conn.commit()
                success_count += 1
                print(f"✅ Facture {numero} synchronisée")
            else:
                print(f"⚠️ Pas de connexion pour facture {numero}")
        
        conn.close()
        
        print(f"\n✅ {success_count} factures synchronisées")
        return success_count == len(factures_pending)
            
    def ajouter_colonne_prix_ht():
        import sqlite3
        # ⭐ CORRECTION : assigner la connexion à une variable
        conn = sqlite3.connect('facturos_mobile.db')
        cursor = conn.cursor()
        
        try:
            # Vérifier si la colonne existe déjà
            cursor.execute("PRAGMA table_info(lignes_facture)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'prix_ht_unitaire' not in columns:
                print("➕ Ajout de la colonne 'prix_ht_unitaire'...")
                cursor.execute("ALTER TABLE lignes_facture ADD COLUMN prix_ht_unitaire REAL DEFAULT 0")
                conn.commit()
                print("✅ Colonne ajoutée avec succès")
            else:
                print("ℹ️ La colonne existe déjà")
                
        except Exception as e:
            print(f"❌ Erreur: {e}")
            conn.rollback()
        finally:
            conn.close()

    # Exécuter
    ajouter_colonne_prix_ht()

# ============================================================================
# ACTIONS SUR FACTURE
# ============================================================================            
            
class InvoiceActions:
    """Classe pour les actions sur les factures (PDF, WhatsApp, Email)"""
    
    def __init__(self, app):
        self.app = app
        self.db = app.db
    
    def generer_pdf_facture(self, facture_id, facture_numero, client_nom, client_tel, client_email, client_adresse, client_ville):
        """Génère un PDF de la facture - VERSION CORRIGÉE AVEC UTILISATEUR CONNECTÉ"""
        try:
            from fpdf import FPDF
            from fpdf.enums import XPos, YPos
            import os
            from datetime import datetime
            from kivy.app import App
            
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            print("\n" + "="*60)
            print("🔍 GÉNÉRATION PDF FACTURE")
            print("="*60)
            print(f"Facture ID reçu: {facture_id}")
            print(f"Facture Numéro: {facture_numero}")
            
            # ⭐ CORRECTION 1: Récupérer la facture par NUMÉRO (plus fiable)
            cursor.execute("""
                SELECT f.id, f.numero, f.date, f.total_ht, f.total_tva, f.total_ttc, 
                       f.statut, f.mode_paiement, f.montant_paye, f.reste_a_payer
                FROM factures f
                WHERE f.numero = ?
            """, (facture_numero,))
            facture = cursor.fetchone()
            
            if not facture:
                print(f"❌ Facture non trouvée par numéro: {facture_numero}")
                conn.close()
                return None
            
            # ⭐ Utiliser le vrai ID de la base
            vrai_facture_id = facture[0]
            numero = facture[1]
            date_facture = facture[2]
            total_ht = facture[3] if facture[3] is not None else 0
            total_tva = facture[4] if facture[4] is not None else 0
            total_ttc = facture[5] if facture[5] is not None else 0
            statut = facture[6] if facture[6] else 'payée'
            mode_paiement = facture[7] if facture[7] else 'Espèces'
            montant_paye = facture[8] if facture[8] else 0
            reste_a_payer = facture[9] if facture[9] else 0
            
            print(f"Facture trouvée: ID={vrai_facture_id}, Numéro={numero}")
            print(f"📊 Facture: {numero}")
            print(f"   Date: {date_facture}")
            print(f"   Total HT: {total_ht}")
            print(f"   Total TVA: {total_tva}")
            print(f"   Total TTC: {total_ttc}")
            
            # ⭐ CORRECTION 2: Récupérer les lignes avec le BON ID
            cursor.execute("""
                SELECT 
                    lf.id,
                    p.nom as produit_nom,
                    lf.quantite,
                    lf.prix_unitaire,
                    lf.total_ligne,
                    p.description,
                    lf.taux_tva as taux_tva,
                    lf.montant_tva
                FROM lignes_facture lf
                JOIN produits p ON lf.produit_id = p.id
                WHERE lf.facture_id = ?
                ORDER BY lf.id
            """, (vrai_facture_id,))
            lignes = cursor.fetchall()
            
            print(f"\n📦 Lignes trouvées: {len(lignes)}")
            for ligne in lignes:
                print(f"   - {ligne[1]}: {ligne[2]} x {ligne[3]:,.0f} = {ligne[4]:,.0f} (TVA {ligne[6]}%)")
            
            # ⭐ CORRECTION 3: Si aucune ligne, vérifier dans la table lignes_facture
            if len(lignes) == 0:
                print("⚠️ Aucune ligne trouvée, vérification directe...")
                cursor.execute("SELECT COUNT(*) FROM lignes_facture")
                total_lignes = cursor.fetchone()[0]
                print(f"   Total lignes dans la base: {total_lignes}")
                
                if total_lignes > 0:
                    cursor.execute("SELECT id, facture_id, produit_id, quantite FROM lignes_facture LIMIT 5")
                    sample = cursor.fetchall()
                    print(f"   Échantillon lignes: {sample}")
            
            # 3. Recalcul si nécessaire
            if total_ht == 0 and len(lignes) > 0:
                print("\n⚠️ total_ht = 0, recalcul à partir des lignes...")
                total_ht_calc = 0
                total_tva_calc = 0
                for ligne in lignes:
                    quantite = ligne[2]
                    prix_unitaire = ligne[3]
                    taux_tva = ligne[6] if len(ligne) > 6 else 0
                    
                    ligne_ht = prix_unitaire * quantite
                    ligne_tva = ligne_ht * (taux_tva / 100)
                    
                    total_ht_calc += ligne_ht
                    total_tva_calc += ligne_tva
                    
                    print(f"   {ligne[1]}: {quantite} x {prix_unitaire:,.0f} = {ligne_ht:,.0f} (TVA {taux_tva}%: {ligne_tva:,.0f})")
                
                total_ttc_calc = total_ht_calc + total_tva_calc
                
                print(f"\n📊 Totaux recalculés: HT={total_ht_calc}, TVA={total_tva_calc}, TTC={total_ttc_calc}")
                
                # Mettre à jour la base
                cursor.execute("""
                    UPDATE factures 
                    SET total_ht = ?, total_tva = ?, total_ttc = ?
                    WHERE id = ?
                """, (total_ht_calc, total_tva_calc, total_ttc_calc, vrai_facture_id))
                conn.commit()
                
                total_ht = total_ht_calc
                total_tva = total_tva_calc
                total_ttc = total_ttc_calc
            
            # 4. Récupérer les paramètres de l'entreprise
            cursor.execute("""
                SELECT nom, adresse, telephone, email, nif, registre_commerce, securite_sociale
                FROM parametres_entreprise LIMIT 1
            """)
            entreprise = cursor.fetchone()
            
            # ⭐ Récupérer l'utilisateur connecté
            app = App.get_running_app()
            utilisateur = ""
            if hasattr(app, 'user_data') and app.user_data:
                utilisateur = app.user_data.get('full_name') or app.user_data.get('username') or ""
            if not utilisateur:
                utilisateur = "Utilisateur"
            
            conn.close()
            
            nom_entreprise = entreprise[0] if entreprise and entreprise[0] else "FACTUROS"
            adresse_entreprise = entreprise[1] if entreprise and entreprise[1] else ""
            telephone_entreprise = entreprise[2] if entreprise and entreprise[2] else ""
            email_entreprise = entreprise[3] if entreprise and entreprise[3] else ""
            nif = entreprise[4] if entreprise and entreprise[4] else ""
            registre_commerce = entreprise[5] if entreprise and entreprise[5] else ""
            securite_sociale = entreprise[6] if entreprise and entreprise[6] else ""
            
            # 5. Création du PDF
            pdf = FPDF()
            pdf.add_page()
            
            # RÉGLAGES
            ligne_height = 5
            petit_espace = 2
            moyen_espace = 3
            
            # ====================================================================
            # EN-TÊTE
            # ====================================================================
            text_start_x = 10
            text_y = 8
            
            pdf.set_xy(text_start_x, text_y)
            pdf.set_font('helvetica', 'B', 16)
            pdf.cell(0, ligne_height, nom_entreprise, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            # Informations légales
            pdf.set_font('helvetica', '', 8)
            infos_legales = []
            if nif:
                infos_legales.append(f"NIF: {nif}")
            if registre_commerce:
                infos_legales.append(f"RC: {registre_commerce}")
            if securite_sociale:
                infos_legales.append(f"SS: {securite_sociale}")
            
            if infos_legales:
                pdf.set_x(text_start_x)
                pdf.cell(0, ligne_height-1, " | ".join(infos_legales), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            # Contact
            infos_contact = []
            if adresse_entreprise:
                infos_contact.append(adresse_entreprise[:40])
            if telephone_entreprise:
                infos_contact.append(f"Tel: {telephone_entreprise}")
            if email_entreprise:
                infos_contact.append(email_entreprise)
            
            if infos_contact:
                pdf.set_x(text_start_x)
                pdf.cell(0, ligne_height-1, " | ".join(infos_contact[:2]), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            # Ligne séparation
            pdf.ln(petit_espace)
            current_y = pdf.get_y()
            pdf.set_line_width(0.3)
            pdf.line(10, current_y, 200, current_y)
            pdf.ln(petit_espace)
            
            # ====================================================================
            # TITRE
            # ====================================================================
            pdf.set_font('helvetica', 'B', 14)
            pdf.cell(0, ligne_height, 'FACTURE', new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            pdf.set_font('helvetica', 'B', 12)
            pdf.cell(0, ligne_height, f"N° {numero}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            # ⭐ Date de génération avec l'utilisateur connecté
            pdf.set_font('helvetica', '', 8)
            pdf.cell(0, ligne_height-1, f"Genere le {datetime.now().strftime('%d/%m/%Y a %H:%M')}     Par: {utilisateur}", 
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.ln(petit_espace)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(petit_espace)
            
            # ====================================================================
            # INFORMATIONS FACTURE ET CLIENT
            # ====================================================================
            pdf.set_font('helvetica', 'B', 10)
            pdf.cell(95, ligne_height, "INFORMATIONS FACTURE", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(95, ligne_height, "INFORMATIONS CLIENT", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            pdf.set_font('helvetica', '', 9)
            date_formatted = date_facture.split()[0] if date_facture else 'N/A'
            
            pdf.cell(95, ligne_height, f"Date: {date_formatted}", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(95, ligne_height, f"Nom: {client_nom or 'Non specifie'}", 
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            pdf.cell(95, ligne_height, f"Paiement: {mode_paiement or 'Non specifie'}", 
                    new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(95, ligne_height, f"Statut: {statut.capitalize()}", 
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            # Informations client supplémentaires
            client_info = []
            if client_tel:
                client_info.append(f"Tel: {client_tel}")
            if client_email:
                client_info.append(f"Email: {client_email}")
            if client_adresse:
                client_info.append(f"Adr: {client_adresse[:30]}")
            if client_ville:
                client_info.append(client_ville)
            
            if client_info:
                client_text = " | ".join(client_info[:2])
                pdf.cell(95, ligne_height, "", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                pdf.cell(95, ligne_height, client_text, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            pdf.ln(petit_espace)
            
            # ====================================================================
            # SECTION PAIEMENT - VERSION CORRIGÉE AVEC STATUT
            # ====================================================================
            pdf.set_font('helvetica', 'B', 9)
            pdf.cell(0, ligne_height, "DETAILS PAIEMENT:", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            pdf.set_font('helvetica', '', 8)

            # Montant total
            pdf.cell(0, ligne_height, f"Total a payer: {total_ttc:,.0f} Fbu", 
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')

            # ⭐ NOUVEAU : Gestion du statut basée sur le champ statut de la base
            if statut == "annulée" or statut == "annulee" or statut == "Annulée":
                pdf.set_text_color(255, 0, 0)  # Rouge
                pdf.cell(0, ligne_height, "Montant paye: 0 Fbu", 
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                pdf.cell(0, ligne_height, "Statut: FACTURE ANNULEE", 
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                pdf.set_text_color(0, 0, 0)
                
            elif statut == "en attente" or statut == "attente" or statut == "En attente":
                pdf.set_text_color(255, 165, 0)  # Orange
                pdf.cell(0, ligne_height, "Montant paye: 0 Fbu", 
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                pdf.cell(0, ligne_height, "Statut: EN ATTENTE DE PAIEMENT", 
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                pdf.set_text_color(0, 0, 0)
                
            else:
                # Pour les factures actives (payée, partielle, non payée)
                pdf.cell(0, ligne_height, f"Montant paye: {montant_paye:,.0f} Fbu", 
                        new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                
                if reste_a_payer > 0:
                    pdf.set_text_color(255, 0, 0)  # Rouge
                    pdf.cell(0, ligne_height, f"Reste a payer: {reste_a_payer:,.0f} Fbu", 
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                    
                    if montant_paye == 0:
                        pdf.cell(0, ligne_height, "Statut: NON PAYE", 
                                new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                    else:
                        pdf.cell(0, ligne_height, "Statut: PAYEMENT PARTIEL", 
                                new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                else:
                    pdf.set_text_color(0, 150, 0)  # Vert
                    pdf.cell(0, ligne_height, "Statut: ENTIEREMENT PAYEE", 
                            new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')

            pdf.set_text_color(0, 0, 0)  # Remettre en noir


            # ====================================================================
            # TABLEAU ARTICLES
            # ====================================================================
            pdf.set_font('helvetica', 'B', 10)
            pdf.cell(0, ligne_height, "ARTICLES:", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            # En-têtes du tableau
            pdf.set_font('helvetica', 'B', 8)
            pdf.cell(90, ligne_height, "Produit", border=1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
            pdf.cell(20, ligne_height, "Qte", border=1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
            pdf.cell(35, ligne_height, "Prix Unitaire", border=1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
            pdf.cell(35, ligne_height, "Total", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            # Contenu du tableau
            pdf.set_font('helvetica', '', 8)
            for ligne in lignes:
                nom_produit = ligne[1][:35] if ligne[1] else 'Produit'
                quantite = ligne[2]
                prix_unitaire = ligne[3]
                total_ligne = ligne[4]
                
                pdf.cell(90, ligne_height+1, nom_produit, border=1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                pdf.cell(20, ligne_height+1, str(quantite), border=1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
                pdf.cell(35, ligne_height+1, f"{prix_unitaire:,.0f}", border=1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='R')
                pdf.cell(35, ligne_height+1, f"{total_ligne:,.0f}", border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            # ====================================================================
            # TOTAUX
            # ====================================================================
            pdf.ln(petit_espace)
            pdf.line(120, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(1)
            
            pdf.set_font('helvetica', 'B', 9)
            pdf.cell(120, ligne_height, "Sous-total HT:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='R')
            pdf.cell(30, ligne_height, f"{total_ht:,.0f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.cell(120, ligne_height, "TVA:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='R')
            pdf.cell(30, ligne_height, f"{total_tva:,.0f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.line(120, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(1)
            
            pdf.set_font('helvetica', 'B', 10)
            pdf.cell(120, ligne_height+1, "TOTAL TTC:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='R')
            pdf.cell(30, ligne_height+1, f"{total_ttc:,.0f} Fbu", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            pdf.line(120, pdf.get_y(), 200, pdf.get_y())
            
            # ====================================================================
            # PIED DE PAGE
            # ====================================================================
            pdf.ln(moyen_espace)
            pdf.line(10, pdf.get_y(), 200, pdf.get_y())
            pdf.ln(petit_espace)
            
            pdf.set_font('helvetica', 'B', 8)
            pdf.cell(0, ligne_height, "MERCI POUR VOTRE CONFIANCE !", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pied_page = [nom_entreprise[:30]]
            if adresse_entreprise:
                pied_page.append(adresse_entreprise[:30])
            if telephone_entreprise:
                pied_page.append(telephone_entreprise)
            
            if pied_page:
                pdf.set_font('helvetica', '', 7)
                pdf.cell(0, ligne_height-1, " - ".join(pied_page), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            # Sauvegarde
            save_folder = "factures_pdf"
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)
            
            filename = f"Facture_{facture_numero}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            filepath = os.path.join(save_folder, filename)
            pdf.output(filepath)
            
            print(f"\n✅ PDF généré: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"❌ Erreur génération PDF: {e}")
            import traceback
            traceback.print_exc()
            return None
            
    def generer_ticket_caisse(self, facture_id, facture_numero, client_nom, client_tel, client_email, client_adresse, client_ville):
        """Génère un ticket de caisse (format 58mm) pour imprimante thermique"""
        try:
            from fpdf import FPDF
            from fpdf.enums import XPos, YPos
            import os
            from datetime import datetime
            from kivy.app import App
            
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            print("\n" + "="*60)
            print("🎫 GÉNÉRATION TICKET DE CAISSE")
            print("="*60)
            print(f"Facture Numéro: {facture_numero}")
            
            # Récupérer la facture
            cursor.execute("""
                SELECT f.id, f.numero, f.date, f.total_ht, f.total_tva, f.total_ttc, 
                       f.statut, f.mode_paiement, f.montant_paye, f.reste_a_payer
                FROM factures f
                WHERE f.numero = ?
            """, (facture_numero,))
            facture = cursor.fetchone()
            
            if not facture:
                print(f"❌ Facture non trouvée: {facture_numero}")
                conn.close()
                return None
            
            vrai_facture_id = facture[0]
            numero = facture[1]
            date_facture = facture[2]
            total_ht = facture[3] if facture[3] is not None else 0
            total_tva = facture[4] if facture[4] is not None else 0
            total_ttc = facture[5] if facture[5] is not None else 0
            statut = facture[6] if facture[6] else 'payée'
            mode_paiement = facture[7] if facture[7] else 'Espèces'
            montant_paye = facture[8] if facture[8] else 0
            reste_a_payer = facture[9] if facture[9] else 0
            
            # Récupérer les lignes
            cursor.execute("""
                SELECT 
                    p.nom as produit_nom,
                    lf.quantite,
                    lf.prix_unitaire,
                    lf.total_ligne
                FROM lignes_facture lf
                JOIN produits p ON lf.produit_id = p.id
                WHERE lf.facture_id = ?
                ORDER BY lf.id
            """, (vrai_facture_id,))
            lignes = cursor.fetchall()
            
            # Récupérer les paramètres de l'entreprise
            cursor.execute("""
                SELECT nom, adresse, telephone, email, nif, registre_commerce, securite_sociale, slogan
                FROM parametres_entreprise LIMIT 1
            """)
            entreprise = cursor.fetchone()
            
            # Récupérer l'utilisateur connecté
            app = App.get_running_app()
            utilisateur = ""
            if hasattr(app, 'user_data') and app.user_data:
                utilisateur = app.user_data.get('full_name') or app.user_data.get('username') or ""
            if not utilisateur:
                utilisateur = "Utilisateur"
            
            conn.close()
            
            nom_entreprise = entreprise[0] if entreprise and entreprise[0] else "FACTUROS"
            adresse_entreprise = entreprise[1] if entreprise and entreprise[1] else ""
            telephone_entreprise = entreprise[2] if entreprise and entreprise[2] else ""
            email_entreprise = entreprise[3] if entreprise and entreprise[3] else ""
            nif = entreprise[4] if entreprise and entreprise[4] else ""
            slogan = entreprise[7] if len(entreprise) > 7 and entreprise[7] else ""
            
            # Création du PDF format ticket (58mm = 210px ~ 74mm)
            pdf = FPDF(orientation='P', unit='mm', format=(80, 297))
            pdf.add_page()
            
            # RÉGLAGES POUR TICKET
            pdf.set_auto_page_break(auto=True, margin=5)
            pdf.set_margins(5, 5, 5)
            
            # Polices
            pdf.set_font('helvetica', '', 9)
            ligne_height = 5
            
            # ====================================================================
            # EN-TÊTE TICKET
            # ====================================================================
            pdf.set_font('helvetica', 'B', 12)
            pdf.cell(0, 7, nom_entreprise, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.set_font('helvetica', '', 7)
            if slogan:
                pdf.cell(0, 4, slogan, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            if adresse_entreprise:
                pdf.cell(0, 4, adresse_entreprise[:40], new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            if telephone_entreprise:
                pdf.cell(0, 4, f"Tel: {telephone_entreprise}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            if nif:
                pdf.cell(0, 4, f"NIF: {nif}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.ln(3)
            
            # Ligne séparation
            pdf.set_line_width(0.3)
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            pdf.ln(3)
            
            # ====================================================================
            # TITRE TICKET
            # ====================================================================
            pdf.set_font('helvetica', 'B', 11)
            pdf.cell(0, 6, "TICKET DE CAISSE", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.set_font('helvetica', '', 8)
            pdf.cell(0, 4, f"N° {numero}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.cell(0, 4, f"Date: {datetime.now().strftime('%d/%m/%Y %H:%M')}", 
                    new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.cell(0, 4, f"Caissier: {utilisateur}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.ln(3)
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            pdf.ln(3)
            
            # ====================================================================
            # INFOS CLIENT
            # ====================================================================
            if client_nom:
                pdf.set_font('helvetica', 'B', 8)
                pdf.cell(0, 4, "CLIENT:", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                pdf.set_font('helvetica', '', 8)
                pdf.cell(0, 4, client_nom[:30], new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
                
                if client_tel:
                    pdf.cell(0, 4, f"Tel: {client_tel}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='L')
            
            pdf.ln(2)
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            pdf.ln(3)
            
            # ====================================================================
            # ARTICLES
            # ====================================================================
            pdf.set_font('helvetica', 'B', 8)
            pdf.cell(40, 5, "ARTICLE", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(10, 5, "Qte", new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
            pdf.cell(20, 5, "Prix", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            
            pdf.set_font('helvetica', '', 8)
            for ligne in lignes:
                nom_produit = ligne[0][:25] if ligne[0] else 'Produit'
                quantite = ligne[1]
                prix_unitaire = ligne[2]
                total_ligne = ligne[3]
                
                pdf.cell(40, 5, nom_produit, new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                pdf.cell(10, 5, str(quantite), new_x=XPos.RIGHT, new_y=YPos.TOP, align='C')
                pdf.cell(20, 5, f"{prix_unitaire:,.0f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
                
                # Ligne de détail si plusieurs produits
                pdf.cell(70, 3, f"  -> {total_ligne:,.0f} Fbu", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.ln(2)
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            
            # ====================================================================
            # TOTAUX
            # ====================================================================
            pdf.ln(2)
            
            pdf.set_font('helvetica', '', 8)
            pdf.cell(50, 4, "Sous-total HT:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(20, 4, f"{total_ht:,.0f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.cell(50, 4, "TVA:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(20, 4, f"{total_tva:,.0f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            
            pdf.set_font('helvetica', 'B', 10)
            pdf.cell(50, 6, "TOTAL TTC:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(20, 6, f"{total_ttc:,.0f} Fbu", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
            
            pdf.line(5, pdf.get_y(), 75, pdf.get_y())
            
            # ====================================================================
            # PAIEMENT - VERSION CORRIGÉE AVEC STATUT
            # ====================================================================
            pdf.ln(2)

            pdf.set_font('helvetica', '', 8)

            # Montant total
            pdf.cell(50, 4, "Montant total:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(20, 4, f"{total_ttc:,.0f} Fbu", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')

            # Montant payé (toujours affiché)
            pdf.cell(50, 4, "Montant paye:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
            pdf.cell(20, 4, f"{montant_paye:,.0f} Fbu", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')

            # ⭐ NOUVEAU : Gestion du statut basée sur le champ statut de la base
            if statut == "annulée" or statut == "annulee" or statut == "Annulée":
                pdf.set_text_color(255, 0, 0)  # Rouge
                pdf.cell(50, 4, "Statut:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                pdf.cell(20, 4, "ANNULEE", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
                
            elif statut == "en attente" or statut == "attente" or statut == "En attente":
                pdf.set_text_color(255, 165, 0)  # Orange
                pdf.cell(50, 4, "Statut:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                pdf.cell(20, 4, "EN ATTENTE", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
                
            else:
                # Pour les factures actives (payée, partielle, non payée)
                if reste_a_payer > 0:
                    pdf.set_text_color(255, 0, 0)  # Rouge
                    pdf.cell(50, 4, "Reste a payer:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                    pdf.cell(20, 4, f"{reste_a_payer:,.0f} Fbu", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
                    
                    if montant_paye == 0:
                        pdf.cell(50, 4, "Statut:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                        pdf.cell(20, 4, "NON PAYE", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
                    else:
                        pdf.cell(50, 4, "Statut:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                        pdf.cell(20, 4, "PARTIEL", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')
                else:
                    pdf.set_text_color(0, 150, 0)  # Vert
                    pdf.cell(50, 4, "Statut:", new_x=XPos.RIGHT, new_y=YPos.TOP, align='L')
                    pdf.cell(20, 4, "PAYE", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='R')

            pdf.set_text_color(0, 0, 0)  # Remettre en noir
            
            # ====================================================================
            # PIED TICKET
            # ====================================================================
            pdf.ln(3)
            
            pdf.set_font('helvetica', 'B', 8)
            pdf.cell(0, 5, "MERCI DE VOTRE VISITE!", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.set_font('helvetica', '', 7)
            pdf.cell(0, 4, "A bientot!", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.ln(3)
            
            if telephone_entreprise:
                pdf.cell(0, 4, f"Tel: {telephone_entreprise}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            pdf.cell(0, 4, datetime.now().strftime('%d/%m/%Y %H:%M'), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            # Lignes de coupe pour ticket
            pdf.ln(5)
            pdf.set_font('helvetica', '', 6)
            pdf.cell(0, 3, "-" * 35, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            pdf.cell(0, 3, "COUPEZ ICI", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            pdf.cell(0, 3, "-" * 35, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
            
            # Sauvegarde
            save_folder = "tickets_pdf"
            if not os.path.exists(save_folder):
                os.makedirs(save_folder)
            
            filename = f"Ticket_{facture_numero}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
            filepath = os.path.join(save_folder, filename)
            pdf.output(filepath)
            
            print(f"\n✅ Ticket généré: {filepath}")
            return filepath
            
        except Exception as e:
            print(f"❌ Erreur génération ticket: {e}")
            import traceback
            traceback.print_exc()
            return None            

    def generer_ticket(self, facture_id, facture_numero, client_nom, client_tel, client_email, client_adresse, client_ville):
        """Génère et ouvre le ticket de caisse"""
        filepath = self.generer_ticket_caisse(facture_id, facture_numero, client_nom, client_tel, 
                                               client_email, client_adresse, client_ville)
        if filepath:
            import os
            os.startfile(filepath)  # Windows
            # Pour Linux/Mac: import subprocess; subprocess.call(['xdg-open', filepath])            
        
    def visualiser_pdf(self, filepath):
        """Visualise le PDF"""
        if filepath and os.path.exists(filepath):
            import subprocess
            import platform
            if platform.system() == 'Windows':
                os.startfile(filepath)
            else:
                subprocess.run(['open', filepath])
                
    def envoyer_whatsapp(self, filepath, facture_numero, client_telephone):
        """Envoie la facture par WhatsApp"""
        if not client_telephone:
            return False
        
        # Nettoyer le numéro
        import re
        phone_clean = re.sub(r'[^0-9+]', '', str(client_telephone))
        if not phone_clean.startswith('+') and not phone_clean.startswith('00'):
            phone_clean = f'+257{phone_clean}'
        
        # Message
        message = f"🧾 *FACTURE {facture_numero}*\n\n"
        message += "Bonjour,\n\n"
        message += "Veuillez trouver ci-joint votre facture.\n\n"
        message += "Merci de votre confiance !\n\n"
        message += "_Facturos - Gestion commerciale_"
        
        import urllib.parse
        encoded_msg = urllib.parse.quote(message)
        
        whatsapp_url = f"https://wa.me/{phone_clean}?text={encoded_msg}"
        import webbrowser
        webbrowser.open(whatsapp_url)
        
        return True
    
    def envoyer_email(self, filepath, facture_numero, client_email):
        """Envoie la facture par Email - Version unifiée et optimisée"""
        if not client_email:
            self.show_message("Erreur", "Adresse email non disponible")
            return False
        
        try:
            from kivy.utils import platform
            import webbrowser
            import urllib.parse
            import subprocess
            import os
            
            # Vérifier si le fichier existe
            if not os.path.exists(filepath):
                self.show_message("Erreur", "Fichier PDF introuvable")
                return False
            
            # Préparer le contenu de l'email
            sujet = f"Facture {facture_numero}"
            corps = f"""Bonjour,

    Veuillez trouver ci-joint votre facture {facture_numero}.

    Merci de votre confiance !

    Cordialement,
    Facturos"""
            
            # ========== PLATEFORME ANDROID ==========
            if platform == 'android':
                # Essayer d'abord avec plyer (plus simple)
                try:
                    from plyer import email
                    email.send(
                        recipient=client_email,
                        subject=sujet,
                        text=corps,
                        create_chooser=True,
                        attachment=filepath
                    )
                    self.show_message("Succès", "Email préparé avec succès")
                    return True
                except Exception as e:
                    print(f"⚠️ Erreur plyer Android: {e}")
                    
                    # Fallback avec les intents Android natifs
                    try:
                        from android.permissions import request_permissions, Permission
                        from android.activity import startActivityForResult
                        import android
                        
                        # Demander les permissions
                        request_permissions([Permission.READ_EXTERNAL_STORAGE, Permission.WRITE_EXTERNAL_STORAGE])
                        
                        # Créer l'intent de partage
                        intent = android.Intent(
                            action=android.Intent.ACTION_SEND,
                            type_='application/pdf'
                        )
                        intent.put_extra(android.Intent.EXTRA_EMAIL, [client_email])
                        intent.put_extra(android.Intent.EXTRA_SUBJECT, sujet)
                        intent.put_extra(android.Intent.EXTRA_TEXT, corps)
                        
                        # Ajouter la pièce jointe
                        from android.net import Uri
                        uri = Uri.from_file(android.JavaObject('java.io.File', filepath))
                        intent.put_extra(android.Intent.EXTRA_STREAM, uri)
                        
                        # Ouvrir le sélecteur d'application
                        startActivityForResult(
                            android.Intent.createChooser(intent, "Envoyer par email"),
                            0
                        )
                        
                        self.show_message("Succès", "Sélectionnez votre application email")
                        return True
                        
                    except Exception as e2:
                        print(f"⚠️ Erreur intent Android: {e2}")
                        # Dernier recours
                        return self._fallback_email(filepath, client_email, sujet, corps)
            
            # ========== PLATEFORME iOS ==========
            elif platform == 'ios':
                # Essayer d'abord avec plyer
                try:
                    from plyer import email
                    email.send(
                        recipient=client_email,
                        subject=sujet,
                        text=corps,
                        create_chooser=True,
                        attachment=filepath
                    )
                    self.show_message("Succès", "Email préparé avec succès")
                    return True
                except:
                    return self._fallback_email(filepath, client_email, sujet, corps)
            
            # ========== PLATEFORMES DESKTOP (Windows, Linux, macOS) ==========
            else:
                # Ouvrir le dossier contenant le PDF
                pdf_dir = os.path.dirname(filepath)
                pdf_name = os.path.basename(filepath)
                
                if platform == 'win':
                    os.startfile(pdf_dir)
                elif platform == 'darwin':  # macOS
                    subprocess.run(['open', pdf_dir])
                else:  # Linux
                    subprocess.run(['xdg-open', pdf_dir])
                
                # Ouvrir l'email avec mailto
                mailto_url = f"mailto:{client_email}?subject={urllib.parse.quote(sujet)}&body={urllib.parse.quote(corps)}"
                webbrowser.open(mailto_url)
                
                self.show_message(
                    "Email", 
                    f"✅ Email préparé pour {client_email}\n\n"
                    f"📄 Joignez le fichier PDF:\n{pdf_name}\n\n"
                    f"📁 Le fichier se trouve dans:\n{pdf_dir}"
                )
                return True
                    
        except Exception as e:
            print(f"❌ Erreur envoi email: {e}")
            import traceback
            traceback.print_exc()
            self.show_message("Erreur", f"Erreur: {str(e)[:100]}")
            return False

    def _fallback_email(self, filepath, client_email, sujet, corps):
        """Méthode de secours pour l'envoi d'email (ouvre le dossier et le mailto)"""
        try:
            import webbrowser
            import urllib.parse
            import subprocess
            import os
            import platform as platform_module
            
            # Ouvrir le dossier contenant le PDF
            pdf_dir = os.path.dirname(filepath)
            pdf_name = os.path.basename(filepath)
            
            if platform_module.system() == 'Windows':
                os.startfile(pdf_dir)
            elif platform_module.system() == 'Darwin':
                subprocess.run(['open', pdf_dir])
            else:
                subprocess.run(['xdg-open', pdf_dir])
            
            # Ouvrir l'email avec mailto
            mailto_url = f"mailto:{client_email}?subject={urllib.parse.quote(sujet)}&body={urllib.parse.quote(corps)}"
            webbrowser.open(mailto_url)
            
            self.show_message(
                "Email", 
                f"✅ Email préparé pour {client_email}\n\n"
                f"📄 Joignez le fichier PDF:\n{pdf_name}\n\n"
                f"📁 Le fichier se trouve dans:\n{pdf_dir}"
            )
            return True
            
        except Exception as e:
            print(f"❌ Erreur fallback email: {e}")
            self.show_message("Info", f"Le PDF est disponible dans:\n{filepath}")
            return False


    
    def show_invoice_actions(self, facture_id, facture_numero, client_nom, client_telephone, client_email, client_adresse, client_ville):
        """Affiche le menu des actions pour une facture"""
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.button import Button
        from kivy.uix.label import Label
        from kivy.clock import Clock
        import os
        
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        title = Label(text=f"Facture: {facture_numero}", font_size=16, bold=True, size_hint_y=None, height=40)
        content.add_widget(title)
        
        client_label = Label(text=f"Client: {client_nom}", font_size=12, size_hint_y=None, height=30)
        content.add_widget(client_label)
        
        buttons = BoxLayout(orientation='vertical', spacing=10, size_hint_y=None)
        buttons.bind(minimum_height=buttons.setter('height'))
        
        # Variable pour stocker le chemin du PDF
        pdf_path = [None]
        
        def generer_et_visualiser(instance):
            popup.dismiss()
            path = self.generer_pdf_facture(facture_id, facture_numero, client_nom, client_telephone, 
                                            client_email, client_adresse, client_ville)
            if path:
                self.visualiser_pdf(path)
                self.show_message("Succès", f"Facture générée: {os.path.basename(path)}")
            else:
                self.show_message("Erreur", "Impossible de générer la facture")
        
        def generer_ticket_action(instance):
            popup.dismiss()
            path = self.generer_ticket_caisse(facture_id, facture_numero, client_nom, client_telephone, 
                                              client_email, client_adresse, client_ville)
            if path:
                self.visualiser_pdf(path)
                self.show_message("Succès", f"Ticket généré: {os.path.basename(path)}")
            else:
                self.show_message("Erreur", "Impossible de générer le ticket")
        
        def generer_et_whatsapp(instance):
            popup.dismiss()
            path = self.generer_pdf_facture(facture_id, facture_numero, client_nom, client_telephone, 
                                            client_email, client_adresse, client_ville)
            if path:
                self.envoyer_whatsapp(path, facture_numero, client_telephone)
                self.show_message("WhatsApp", f"Facture générée\n\nOuvrez WhatsApp et joignez le fichier:\n{os.path.basename(path)}")
            else:
                self.show_message("Erreur", "Impossible de générer la facture")
        
        def generer_et_email(instance):
            popup.dismiss()
            path = self.generer_pdf_facture(facture_id, facture_numero, client_nom, client_telephone, 
                                            client_email, client_adresse, client_ville)
            if path:
                self.envoyer_email(path, facture_numero, client_email)
                self.show_message("Email", f"Facture générée\n\nOuvrez votre email et joignez le fichier:\n{os.path.basename(path)}")
            else:
                self.show_message("Erreur", "Impossible de générer la facture")
        
        # Bouton FACTURE PDF
        pdf_btn = Button(text="VOIR FACTURE PDF", size_hint_y=None, height=50, background_color=(0.2, 0.8, 0.2, 1))
        pdf_btn.bind(on_press=generer_et_visualiser)
        buttons.add_widget(pdf_btn)
        
        # ⭐ NOUVEAU BOUTON TICKET ⭐
        ticket_btn = Button(text="TICKET DE CAISSE", size_hint_y=None, height=50, background_color=(0.2, 0.6, 0.9, 1))
        ticket_btn.bind(on_press=generer_ticket_action)
        buttons.add_widget(ticket_btn)
        
        if client_telephone:
            whatsapp_btn = Button(text="ENVOYER PAR WHATSAPP", size_hint_y=None, height=50, background_color=(0.8, 0.6, 0, 1))
            whatsapp_btn.bind(on_press=generer_et_whatsapp)
            buttons.add_widget(whatsapp_btn)
        
        if client_email:
            email_btn = Button(text="ENVOYER PAR EMAIL", size_hint_y=None, height=50, background_color=(0.2, 0.8, 0.2, 1))
            email_btn.bind(on_press=generer_et_email)
            buttons.add_widget(email_btn)
        
        close_btn = Button(text="FERMER", size_hint_y=None, height=50, background_color=(0.8, 0.3, 0.3, 1))
        close_btn.bind(on_press=lambda x: popup.dismiss())
        buttons.add_widget(close_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="ACTIONS FACTURE", content=content, size_hint=(0.9, 0.7))
        popup.open()
        
            
    def afficher_statut_paiement(self, pdf, statut, total_ttc, montant_paye, reste_a_payer):
        """Affiche correctement le statut de paiement selon le statut réel de la facture"""
        
        # Convertir en minuscule pour comparaison
        statut_lower = statut.lower() if statut else ""
        
        if statut_lower in ["annulée", "annulee"]:
            pdf.set_text_color(255, 0, 0)  # Rouge
            return "ANNULEE"
            
        elif statut_lower in ["en attente", "attente"]:
            pdf.set_text_color(255, 165, 0)  # Orange
            return "EN ATTENTE"
            
        else:
            # Factures actives
            if reste_a_payer > 0:
                pdf.set_text_color(255, 0, 0)  # Rouge
                if montant_paye == 0:
                    return "NON PAYE"
                else:
                    return "PAYEMENT PARTIEL"
            else:
                pdf.set_text_color(0, 150, 0)  # Vert
                return "PAYE"        
        

    def show_message(self, title, message):
        """Affiche un message temporaire"""
        from kivy.uix.popup import Popup
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.button import Button
        from kivy.clock import Clock
        
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 5)            
            
 
# ============================================================================
# CARTE AVEC FOND ARRONDI
# ============================================================================

class RoundedCard(BoxLayout):
    """Widget carte avec fond arrondi"""
    def __init__(self, bg_color=(0.2, 0.6, 0.8, 0.2), **kwargs):
        super().__init__(**kwargs)
        self.bg_color = bg_color
        self.bind(pos=self._update_rect, size=self._update_rect)
        with self.canvas.before:
            Color(*bg_color)
            self.rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(10)])
    
    def _update_rect(self, instance, value):
        if hasattr(self, 'rect'):
            self.rect.pos = instance.pos
            self.rect.size = instance.size


# ============================================================================
# ÉCRAN DE CONNEXION
# ============================================================================

class LoginScreen(Screen):
    """Écran de connexion - Version stable"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical', padding=20, spacing=10)
        
        # Logo
        layout.add_widget(Label(
            text='FACTUROS',
            font_size=32,
            bold=True,
            size_hint=(1, 0.1)
        ))
        
        layout.add_widget(Label(
            text='Application Mobile',
            font_size=14,
            size_hint=(1, 0.05)
        ))
        
        # Formulaire de connexion
        form = BoxLayout(orientation='vertical', spacing=10, size_hint=(1, 0.5))
        
        # Adresse IP
        form.add_widget(Label(text='Adresse IP du serveur:', halign='left'))
        self.ip_input = TextInput(
            text='172.20.10.14',
            multiline=False,
            size_hint=(1, None),
            height=45
        )
        form.add_widget(self.ip_input)
        
        # Port
        form.add_widget(Label(text='Port:', halign='left'))
        self.port_input = TextInput(
            text='65432',
            multiline=False,
            size_hint=(1, None),
            height=45
        )
        form.add_widget(self.port_input)
        
        # Nom d'utilisateur
        form.add_widget(Label(text='Nom d\'utilisateur:', halign='left'))
        self.username_input = TextInput(
            text='BITANGIMANA',
            multiline=False,
            size_hint=(1, None),
            height=45
        )
        form.add_widget(self.username_input)
        
        # Mot de passe
        form.add_widget(Label(text='Mot de passe:', halign='left'))
        self.password_input = TextInput(
            text='',
            password=True,
            multiline=False,
            size_hint=(1, None),
            height=45
        )
        form.add_widget(self.password_input)
        
        layout.add_widget(form)
        
        # Bouton de connexion
        self.connect_btn = Button(
            text='SE CONNECTER',
            size_hint=(1, 0.1),
            background_color=(0.2, 0.8, 0.3, 1),
            font_size=16,
            bold=True
        )
        self.connect_btn.bind(on_press=self.connect)
        layout.add_widget(self.connect_btn)
        
        # Statut
        self.status_label = Label(
            text='Prêt',
            size_hint=(1, 0.1),
            color=(0.5, 0.5, 0.5, 1)
        )
        layout.add_widget(self.status_label)
        
        self.add_widget(layout)
    
    def connect(self, instance):
        """Connexion au serveur"""
        server = self.ip_input.text.strip()
        port = int(self.port_input.text.strip()) if self.port_input.text else 65432
        username = self.username_input.text.strip()
        password = self.password_input.text
        
        if not username or not password:
            self.status_label.text = "Entrez vos identifiants"
            self.status_label.color = (1, 0, 0, 1)
            return
        
        self.status_label.text = f"Connexion à {server}:{port}..."
        self.status_label.color = (1, 1, 0, 1)
        self.connect_btn.disabled = True
        
        # ⭐ AJOUTER UN TIMEOUT GLOBAL
        import socket
        socket.setdefaulttimeout(10)  # Timeout global de 10 secondes
        
        def do_connect():
            app = App.get_running_app()
            
            try:
                success = app.network.connect_to_server(server, port)
                
                if success:
                    auth_success = app.network.authenticate(username, password)
                    if auth_success:
                        # Log asynchrone
                        try:
                            threading.Thread(
                                target=lambda: app.db.add_log(
                                    username, 'connexion', 'Authentification',
                                    f"Connexion réussie à {server}:{port}"
                                ),
                                daemon=True
                            ).start()
                        except:
                            pass
                        
                        Clock.schedule_once(lambda dt: self.on_success(), 0)
                    else:
                        Clock.schedule_once(lambda dt: self.on_error("Identifiants incorrects"), 0)
                else:
                    Clock.schedule_once(lambda dt: self.on_error("Connexion impossible - Vérifiez que le serveur est démarré"), 0)
            except socket.timeout:
                Clock.schedule_once(lambda dt: self.on_error("Délai de connexion dépassé"), 0)
            except Exception as e:
                Clock.schedule_once(lambda dt: self.on_error(f"Erreur: {str(e)[:50]}"), 0)
            finally:
                socket.setdefaulttimeout(None)  # Restaurer le timeout par défaut
        
        threading.Thread(target=do_connect, daemon=True).start()
        
    def on_success(self):
        app = App.get_running_app()
        
        conn = None
        try:
            conn = app.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, role, full_name, email, permissions FROM users WHERE username = ?", 
                           (self.username_input.text,))
            user = cursor.fetchone()
            
            print(f"🔍 [LOGIN] user récupéré: {user}")  # ⭐ LOG
            
            if user:
                app.user_data = {
                    'id': user[0],
                    'username': user[1],
                    'role': user[2],
                    'full_name': user[3] or user[1],
                    'email': user[4] or '',
                    'permissions': json.loads(user[5]) if user[5] else {}
                }
                print(f"✅ [LOGIN] Utilisateur trouvé: {app.user_data}")
            else:
                print(f"⚠️ [LOGIN] Utilisateur {self.username_input.text} non trouvé, création...")
                
                import hashlib
                import uuid
                from datetime import datetime
                
                user_uuid = str(uuid.uuid4())
                hashed_password = hashlib.sha256(self.password_input.text.encode()).hexdigest()
                permissions = PermissionManager.get_default_permissions('admin')
                
                cursor.execute('''
                    INSERT INTO users (username, password, role, full_name, email, is_active, created_at, permissions, uuid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    self.username_input.text,
                    hashed_password,
                    'admin',
                    self.username_input.text,
                    '',
                    1,
                    datetime.now().isoformat(),
                    json.dumps(permissions),
                    user_uuid
                ))
                conn.commit()
                
                cursor.execute("SELECT id FROM users WHERE username = ?", (self.username_input.text,))
                new_user = cursor.fetchone()
                
                app.user_data = {
                    'id': new_user[0],
                    'username': self.username_input.text,
                    'role': 'admin',
                    'full_name': self.username_input.text,
                    'email': '',
                    'permissions': permissions
                }
                print(f"✅ [LOGIN] Utilisateur admin créé: {app.user_data}")
        
        except Exception as e:
            print(f"❌ [LOGIN] Erreur: {e}")
            import traceback
            traceback.print_exc()
            app.user_data = {
                'id': 1,
                'username': self.username_input.text,
                'role': 'admin',
                'full_name': self.username_input.text,
                'email': '',
                'permissions': {}
            }
            print(f"⚠️ [LOGIN] Fallback utilisé: {app.user_data}")
        
        finally:
            if conn:
                conn.close()
        
        print(f"📢 [LOGIN] app.user_data FINAL = {app.user_data}")  # ⭐ LOG IMPORTANT
        
        # ⭐ FORCER LE RAFFRAÎCHISSEMENT DU DASHBOARD
        try:
            dashboard = self.manager.get_screen('dashboard')
            if hasattr(dashboard, 'refresh_buttons'):
                dashboard.refresh_buttons()
                print("✅ Boutons du dashboard rafraîchis")
            dashboard.load_data()
        except Exception as e:
            print(f"❌ Erreur rafraîchissement dashboard: {e}")
        
        self.status_label.text = "Connecté!"
        self.status_label.color = (0, 1, 0, 1)
        self.connect_btn.disabled = False
        self.manager.current = 'dashboard'
        
        
        # Forcer l'envoi de toutes les données locales au serveur
        app = App.get_running_app()
        app.sync_all_local_data()  # Créez cette méthode        
        
        
        
        # Demander la synchronisation
        Clock.schedule_once(lambda dt: app.network.request_sync(), 1)

    def on_error(self, message):
        """Erreur de connexion"""
        self.status_label.text = f"❌ {message}"
        self.status_label.color = (1, 0, 0, 1)
        self.connect_btn.disabled = False


# ============================================================================
# ÉCRAN TABLEAU DE BORD
# ============================================================================

class DashboardScreen(Screen):
    """Écran principal du tableau de bord avec défilement"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ca_today = None
        self.ventes_today = None
        self.stock_faible = None
        self.alertes = None
        self.ca_mois = None
        self.nb_factures = None
        self.total_produits = None
        self.total_clients = None
        self.top_produit = None
        self.user_role = None
        self.buttons_container = None
        self.build_ui()
    
    def build_ui(self):
        main_layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=10)
        header.add_widget(Label(text='FACTUROS MOBILE', font_size=20, bold=True))
        profile_btn = Button(text='PROFIL', size_hint=(0.1, 1), background_color=(0.8, 0.6, 0, 1), font_size=18)
        profile_btn.bind(on_press=self.go_to_profil)
        header.add_widget(profile_btn)
        main_layout.add_widget(header)
        
        # ⭐ ScrollView principal - défilement vertical
        scroll = ScrollView(size_hint=(1, 0.75), do_scroll_x=False, do_scroll_y=True)
        content = BoxLayout(orientation='vertical', spacing=12, padding=10, size_hint_y=None)
        content.bind(minimum_height=content.setter('height'))
        
        # KPI Cards
        self.create_kpi_cards(content)
        
        # Statistiques
        self.create_stats_cards(content)
        
        # Boutons d'action
        self.create_action_buttons(content)
        
        scroll.add_widget(content)
        main_layout.add_widget(scroll)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('NOUVEAU', 'nouvelle_vente'),
            ('PRODUITS', 'produits'),
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12, bold=True)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        main_layout.add_widget(nav)
        
        self.add_widget(main_layout)
    
    def create_kpi_cards(self, parent):
        """Crée les cartes KPI"""
        kpi_layout = GridLayout(cols=2, spacing=10, size_hint_y=None, height=160, padding=[5, 5])
        
        # CA aujourd'hui
        ca_card = RoundedCard(bg_color=(0.2, 0.6, 0.8, 0.5))
        ca_card.add_widget(Label(text='CA AUJOURD\'HUI', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.ca_today = Label(text='0 Fbu', font_size=18, bold=True, color=(0.2, 1, 0.2, 1), size_hint_y=None, height=35)
        ca_card.add_widget(self.ca_today)
        kpi_layout.add_widget(ca_card)
        
        # Ventes aujourd'hui
        ventes_card = RoundedCard(bg_color=(0.2, 0.8, 0.4, 0.5))
        ventes_card.add_widget(Label(text='VENTES', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.ventes_today = Label(text='0', font_size=22, bold=True, color=(1, 1, 0, 1), size_hint_y=None, height=35)
        ventes_card.add_widget(self.ventes_today)
        kpi_layout.add_widget(ventes_card)
        
        # Stock faible
        stock_card = RoundedCard(bg_color=(0.9, 0.5, 0.2, 0.5))
        stock_card.add_widget(Label(text='STOCK FAIBLE', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.stock_faible = Label(text='0', font_size=22, bold=True, color=(1, 0.8, 0.2, 1), size_hint_y=None, height=35)
        stock_card.add_widget(self.stock_faible)
        kpi_layout.add_widget(stock_card)
        
        # Alertes
        alert_card = RoundedCard(bg_color=(0.9, 0.3, 0.3, 0.5))
        alert_card.add_widget(Label(text='ALERTES', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.alertes = Label(text='0', font_size=22, bold=True, color=(1, 0.2, 0.2, 1), size_hint_y=None, height=35)
        alert_card.add_widget(self.alertes)
        kpi_layout.add_widget(alert_card)
        
        parent.add_widget(kpi_layout)
    
    def create_stats_cards(self, parent):
        """Crée les cartes de statistiques"""
        
        title_label = Label(text='STATISTIQUES', font_size=16, bold=True, color=(1, 1, 0, 1), size_hint_y=None, height=35)
        parent.add_widget(title_label)
        
        stats_grid = GridLayout(cols=2, spacing=8, size_hint_y=None, height=180, padding=[5, 5])
        
        # CA mensuel
        ca_mois_card = RoundedCard(bg_color=(0.8, 0.6, 0, 0.5))
        ca_mois_card.add_widget(Label(text='CA Mensuel', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.ca_mois = Label(text='0 Fbu', font_size=14, bold=True, color=(0.2, 1, 0.2, 1), size_hint_y=None, height=30)
        ca_mois_card.add_widget(self.ca_mois)
        stats_grid.add_widget(ca_mois_card)
        
        # Nombre factures
        factures_card = RoundedCard(bg_color=(0.2, 0.8, 0.2, 0.5))
        factures_card.add_widget(Label(text='Factures', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.nb_factures = Label(text='0', font_size=18, bold=True, color=(0.2, 1, 0.2, 1), size_hint_y=None, height=30)
        factures_card.add_widget(self.nb_factures)
        stats_grid.add_widget(factures_card)
        
        # Total produits
        produits_card = RoundedCard(bg_color=(0.6, 0.3, 0.8, 0.5))
        produits_card.add_widget(Label(text='Produits', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.total_produits = Label(text='0', font_size=18, bold=True, color=(0.2, 1, 0.2, 1), size_hint_y=None, height=30)
        produits_card.add_widget(self.total_produits)
        stats_grid.add_widget(produits_card)
        
        # Total clients
        clients_card = RoundedCard(bg_color=(0.2, 0.6, 0.9, 0.5))
        clients_card.add_widget(Label(text='Clients', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.total_clients = Label(text='0', font_size=18, bold=True, color=(0.2, 1, 0.2, 1), size_hint_y=None, height=30)
        clients_card.add_widget(self.total_clients)
        stats_grid.add_widget(clients_card)
        
        parent.add_widget(stats_grid)
        
        # Top produit
        top_produit_card = RoundedCard(bg_color=(0.3, 0.7, 0.4, 0.5), size_hint_y=None, height=55)
        top_produit_card.add_widget(Label(text='Top Produit', font_size=12, bold=True, color=(1, 1, 1, 1), size_hint_y=None, height=25))
        self.top_produit = Label(text='Chargement...', font_size=12, bold=True, color=(1, 1, 0, 1), size_hint_y=None, height=25)
        top_produit_card.add_widget(self.top_produit)
        parent.add_widget(top_produit_card)
        
        parent.add_widget(Widget(size_hint_y=None, height=10))
    
    def create_action_buttons(self, parent):
        """Crée les boutons d'action"""
        app = App.get_running_app()
        self.buttons_container = parent
        
        user_role = app.user_data.get('role') if app.user_data else 'viewer'
        
        btn_layout = GridLayout(cols=2, spacing=10, size_hint_y=None, height=320)  # ⭐ Augmenté la hauteur pour accueillir le nouveau bouton
        
        buttons = [
            ('NOUVELLE VENTE', self.go_to_nouvelle_vente, (0.2, 0.6, 0.9, 1)),
            ('PRODUITS', self.go_to_produits, (0.3, 0.7, 0.4, 1)),
            ('CLIENTS', self.go_to_clients, (0.9, 0.5, 0.2, 1)),
            ('ALERTES', self.go_to_alertes, (0.9, 0.3, 0.3, 1)),
            ('STATS AVANCEES', self.go_to_stats, (0.5, 0.3, 0.8, 1)),
            ('PARAMÈTRES', self.go_to_parametres, (0.4, 0.4, 0.5, 1)),
            ('LOGS', self.go_to_logs, (0.3, 0.5, 0.7, 1)), 
        ]
        
        if user_role == 'admin':
            buttons.append(('UTILISATEURS', self.go_to_users, (0.6, 0.3, 0.8, 1)))
        
        for text, callback, color in buttons:
            btn = Button(text=text, background_color=color, font_size=14, bold=True)
            btn.bind(on_press=callback)
            btn_layout.add_widget(btn)
        
        parent.add_widget(btn_layout)
        print(f"✅ {len(buttons)} boutons créés")
    
    def load_data(self):
        """Charge les données depuis la base locale"""
        app = App.get_running_app()
        db = app.db
        
        try:
            # KPI Cards
            ca_today = db.get_ca_today()
            ventes_today = db.get_ventes_today()
            stock_faible = db.get_stock_faible()
            alertes = db.get_alertes_count()
            
            if self.ca_today:
                self.ca_today.text = f"{ca_today:,.0f} Fbu"
            if self.ventes_today:
                self.ventes_today.text = str(ventes_today)
            if self.stock_faible:
                self.stock_faible.text = str(stock_faible)
            if self.alertes:
                self.alertes.text = str(alertes)
            
            # Stats Cards
            ca_mois = db.get_ca_mois()
            total_factures = db.get_total_factures()
            total_produits = len(db.get_produits())
            total_clients = len(db.get_clients())
            
            if self.ca_mois:
                self.ca_mois.text = f"{ca_mois:,.0f} Fbu"
            if self.nb_factures:
                self.nb_factures.text = str(total_factures)
            if self.total_produits:
                self.total_produits.text = str(total_produits)
            if self.total_clients:
                self.total_clients.text = str(total_clients)
            
            # Top produit
            conn = app.db.get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT p.nom, SUM(lf.quantite) as total_vendu
                FROM lignes_facture lf
                JOIN produits p ON lf.produit_id = p.id
                GROUP BY lf.produit_id
                ORDER BY total_vendu DESC
                LIMIT 1
            """)
            top = cursor.fetchone()
            if self.top_produit:
                if top:
                    self.top_produit.text = f"{top[0]} ({top[1]} vendus)"
                else:
                    self.top_produit.text = "Aucune vente"
            conn.close()
            
        except Exception as e:
            print(f"❌ Erreur load_data: {e}")
    
    def go_to_profil(self, instance):
        self.manager.current = 'profil'
    
    def go_to_ventes(self, instance):
        self.manager.current = 'ventes'
    
    def go_to_nouvelle_vente(self, instance):
        self.manager.current = 'nouvelle_vente'
    
    def go_to_produits(self, instance):
        self.manager.current = 'produits'
    
    def go_to_clients(self, instance):
        self.manager.current = 'clients'
    
    def go_to_alertes(self, instance):
        self.manager.current = 'alertes'
    
    def go_to_stats(self, instance):
        self.manager.current = 'stats_avancees'

    def go_to_parametres(self, instance):
        """Navigue vers l'écran des paramètres"""
        self.manager.current = 'parametres'

    def go_to_logs(self, instance):
        """Va à l'écran des logs d'activité"""
        self.manager.current = 'logs_activite'        

    def go_to_users(self, instance):
        self.manager.current = 'users'
    
    def on_enter(self):
        self.load_data()
    
    def refresh_buttons(self):
        """Rafraîchit les boutons d'action"""
        print("🔄 Rafraîchissement des boutons d'action")
          
# ============================================================================
# ÉCRAN CLIENTS
# ============================================================================

class ClientsScreen(Screen):
    """Écran des clients avec ajout et historique"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.all_clients = []
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='GESTION CLIENTS', font_size=18, bold=True))
        add_btn = Button(text='+', size_hint=(0.15, 1), background_color=(0.2, 0.7, 0.3, 1))
        add_btn.bind(on_press=self.add_client)
        header.add_widget(add_btn)
        layout.add_widget(header)
        
        # Barre de recherche
        search_layout = BoxLayout(size_hint=(1, 0.08), padding=5, spacing=5)
        search_layout.add_widget(Label(text='', size_hint_x=0.1, font_size=18))
        self.search_input = TextInput(
            hint_text='Rechercher un client...',
            multiline=False,
            size_hint_x=0.9,
            font_size=14
        )
        self.search_input.bind(text=self.on_search)
        search_layout.add_widget(self.search_input)
        layout.add_widget(search_layout)
        
        # Liste des clients
        self.scroll = ScrollView()
        self.list_layout = BoxLayout(orientation='vertical', spacing=10, padding=10, size_hint_y=None)
        self.list_layout.bind(minimum_height=self.list_layout.setter('height'))
        self.scroll.add_widget(self.list_layout)
        layout.add_widget(self.scroll)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('NOUVEAU', 'nouvelle_vente'),
            (' CLIENTS', 'clients')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12, bold=True)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        
        self.add_widget(layout)
    
    def add_client(self, instance):
        """Ouvre le formulaire d'ajout de client"""
        self.manager.current = 'client_form'
        self.manager.get_screen('client_form').set_mode('add')
    
    def display_clients(self, clients):
        """Affiche la liste des clients avec bouton historique"""
        self.list_layout.clear_widgets()
        
        if not clients:
            self.list_layout.add_widget(Label(
                text='👥 Aucun client trouvé\n\nAjoutez vos premiers clients !',
                font_size=14,
                color=(0.5, 0.5, 0.5, 1),
                size_hint_y=None,
                height=150,
                halign='center'
            ))
            return
        
        for c in clients:
            # c: id, nom, email, telephone
            nom = c[1] if len(c) > 1 else 'Client sans nom'
            email = c[2] if len(c) > 2 and c[2] else 'N/A'
            telephone = c[3] if len(c) > 3 and c[3] else 'N/A'
            
            # Carte avec bouton historique
            card = BoxLayout(orientation='vertical', size_hint_y=None, height=130, padding=12, spacing=5)
            with card.canvas.before:
                Color(0.3, 0.6, 0.9, 0.2)
                card.rect = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(12)])
            card.bind(pos=self._update_rect, size=self._update_rect)
            
            # Nom
            nom_label = Label(text=f"{nom[:35]}", font_size=16, bold=True, halign='left', size_hint_y=None, height=35)
            nom_label.bind(size=nom_label.setter('text_size'))
            card.add_widget(nom_label)
            
            # Email
            email_label = Label(text=f"{email}", font_size=12, halign='left', size_hint_y=None, height=25)
            email_label.bind(size=email_label.setter('text_size'))
            card.add_widget(email_label)
            
            # Téléphone
            tel_label = Label(text=f"{telephone}", font_size=12, halign='left', size_hint_y=None, height=25)
            tel_label.bind(size=tel_label.setter('text_size'))
            card.add_widget(tel_label)
            
            # Boutons d'action
            action_bar = BoxLayout(size_hint_y=None, height=35, spacing=5)
            
            detail_btn = Button(text="DÉTAIL", size_hint_x=0.5, font_size=12, background_color=(0.2, 0.6, 0.9, 1))
            detail_btn.bind(on_press=lambda x, cid=c[0], data=c: self.go_to_detail(cid, data))
            action_bar.add_widget(detail_btn)
            
            history_btn = Button(text="HISTORIQUE", size_hint_x=0.5, font_size=12, background_color=(0.5, 0.3, 0.8, 1))
            history_btn.bind(on_press=lambda x, cid=c[0], name=nom: self.go_to_history(cid, name))
            action_bar.add_widget(history_btn)
            
            card.add_widget(action_bar)
            
            self.list_layout.add_widget(card)
    
    def go_to_detail(self, client_id, client_data):
        """Va à l'écran de détail"""
        self.manager.get_screen('client_detail').set_client(client_id, client_data)
        self.manager.current = 'client_detail'
    
    def go_to_history(self, client_id, client_name):
        """Va à l'écran d'historique"""
        self.manager.get_screen('client_history').set_client(client_id, client_name)
        self.manager.current = 'client_history'
    
    def on_enter(self):
        """Quand on arrive sur l'écran"""
        # ⭐⭐⭐ LOG DE TEST ⭐⭐⭐
        app = App.get_running_app()
        try:
            app.db.add_log(
                app.user_data.get('username', 'Utilisateur'),
                'client',
                'Clients',
                "Accès à la liste des clients"
            )
        except Exception as e:
            print(f"⚠️ Erreur log: {e}")        
        self.load_clients()
    
    def load_clients(self):
        app = App.get_running_app()
        db = app.db
        self.all_clients = db.get_clients()
        self.display_clients(self.all_clients)
    
    def on_search(self, instance, value):
        if not value:
            self.display_clients(self.all_clients)
            return
        filtered = [c for c in self.all_clients if value.lower() in c[1].lower()]
        self.display_clients(filtered)
    
    def _update_rect(self, instance, value):
        if hasattr(instance, 'rect'):
            instance.rect.pos = instance.pos
            instance.rect.size = instance.size

    def on_client_click(self, instance, touch):
        """Gère le clic sur un client"""
        if instance.collide_point(*touch.pos):
            if hasattr(instance, 'client_id') and hasattr(instance, 'client_data'):
                client_id = instance.client_id
                client_nom = instance.client_data[1] if len(instance.client_data) > 1 else "Client"
                
                # ⭐⭐⭐ AJOUTER LE LOG CONSULTATION ⭐⭐⭐
                app = App.get_running_app()
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'client_consultation',
                        'Clients',
                        f"Consultation client: {client_nom}"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log: {e}")
                
                detail_screen = self.manager.get_screen('client_detail')
                detail_screen.set_client(instance.client_id, instance.client_data)
                self.manager.current = 'client_detail'
                return True
        return False         
            

    def go_back(self, instance):
        self.manager.current = 'dashboard'

# ============================================================================
# HISTORIQUE DES COMMUNICATIONS
# ============================================================================        

class ClientHistoryScreen(Screen):
    """Écran d'historique complet des communications d'un client"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client_id = None
        self.client_name = None
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.08), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.1, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        self.title_label = Label(text='HISTORIQUE', font_size=18, bold=True)
        header.add_widget(self.title_label)
        
        # Boutons d'actions
        btn_export = Button(text='EXP', size_hint=(0.08, 1), font_size=16)
        btn_export.bind(on_press=self.export_history)
        header.add_widget(btn_export)
        
        btn_delete = Button(text='DEL', size_hint=(0.08, 1), font_size=16, background_color=(0.8, 0.2, 0.2, 1))
        btn_delete.bind(on_press=self.clear_all_history)
        header.add_widget(btn_delete)
        
        layout.add_widget(header)
        
        # Filtres
        filter_layout = BoxLayout(size_hint=(1, 0.08), padding=5, spacing=5)
        
        filter_layout.add_widget(Label(text="Type:", size_hint_x=0.2, font_size=12))
        self.type_spinner = Spinner(
            text='Tous',
            values=('Tous', 'appel', 'whatsapp', 'email', 'rappel', 'note'),
            size_hint_x=0.3,
            height=35
        )
        self.type_spinner.bind(text=self.on_filter_change)
        filter_layout.add_widget(self.type_spinner)
        
        filter_layout.add_widget(Label(text="Période:", size_hint_x=0.2, font_size=12))
        self.period_spinner = Spinner(
            text='Toutes',
            values=('Toutes', 'Aujourd\'hui', 'Cette semaine', 'Ce mois', 'Cette année'),
            size_hint_x=0.3,
            height=35
        )
        self.period_spinner.bind(text=self.on_filter_change)
        filter_layout.add_widget(self.period_spinner)
        
        layout.add_widget(filter_layout)
        
        # Barre de recherche
        search_layout = BoxLayout(size_hint=(1, 0.08), padding=5, spacing=5)
        search_layout.add_widget(Label(text="🔍", size_hint_x=0.1, font_size=18))
        self.search_input = TextInput(
            hint_text='Rechercher...',
            multiline=False,
            size_hint_x=0.9,
            font_size=12
        )
        self.search_input.bind(text=self.on_search)
        search_layout.add_widget(self.search_input)
        layout.add_widget(search_layout)
        
        # Statistiques
        self.stats_label = Label(text="", size_hint_y=None, height=40, font_size=12, color=(0.3, 0.6, 0.9, 1))
        layout.add_widget(self.stats_label)
        
        # Liste des communications
        self.scroll = ScrollView()
        self.history_container = BoxLayout(orientation='vertical', size_hint_y=None, spacing=8, padding=10)
        self.history_container.bind(minimum_height=self.history_container.setter('height'))
        self.scroll.add_widget(self.history_container)
        layout.add_widget(self.scroll)
        
        self.add_widget(layout)
    
    def set_client(self, client_id, client_name):
        """Définit le client à afficher"""
        self.client_id = client_id
        self.client_name = client_name
        self.title_label.text = f"HISTORIQUE - {client_name[:20]}"
        self.load_history()
    
    def get_date_filter(self):
        """Retourne la condition SQL pour le filtre de période"""
        period = self.period_spinner.text
        today = datetime.now().strftime('%Y-%m-%d')
        
        if period == "Aujourd'hui":
            return f"date LIKE '{today}%'"
        elif period == "Cette semaine":
            # Début de semaine (lundi)
            start = (datetime.now() - timedelta(days=datetime.now().weekday())).strftime('%Y-%m-%d')
            return f"date >= '{start}'"
        elif period == "Ce mois":
            return f"date LIKE '{datetime.now().strftime('%Y-%m')}%'"
        elif period == "Cette année":
            return f"date LIKE '{datetime.now().strftime('%Y')}%'"
        return "1=1"
    
    def load_history(self):
        """Charge l'historique avec les filtres"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            # Construire la requête avec filtres
            query = """
                SELECT id, date, type, details, notes, statut
                FROM historique_communications
                WHERE client_id = ?
            """
            params = [self.client_id]
            
            # Filtre type
            type_filter = self.type_spinner.text
            if type_filter != 'Tous':
                query += " AND type = ?"
                params.append(type_filter)
            
            # Filtre période
            date_condition = self.get_date_filter()
            query += f" AND {date_condition}"
            
            # Recherche textuelle
            search = self.search_input.text.strip()
            if search:
                query += " AND (details LIKE ? OR notes LIKE ?)"
                search_param = f"%{search}%"
                params.extend([search_param, search_param])
            
            query += " ORDER BY date DESC"
            
            cursor.execute(query, params)
            history = cursor.fetchall()
            
            self.display_history(history)
            self.update_stats(history)
            
        except Exception as e:
            print(f"❌ Erreur load_history: {e}")
        finally:
            conn.close()
    
    def display_history(self, history):
        """Affiche la liste des communications"""
        self.history_container.clear_widgets()
        
        if not history:
            empty_label = Label(
                text="📭 Aucune communication trouvée",
                font_size=14,
                color=(0.5, 0.5, 0.5, 1),
                size_hint_y=None,
                height=100,
                halign='center'
            )
            self.history_container.add_widget(empty_label)
            self.history_container.height = 100
            return
        
        for h in history:
            comm_id, date, comm_type, details, notes, statut = h
            
            # Formater la date
            try:
                date_obj = datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
                date_formatted = date_obj.strftime("%d/%m/%Y à %H:%M")
            except:
                date_formatted = date[:16] if date else ''
            
            type_icon = self.get_type_icon(comm_type)
            status_icon = '✅' if statut == 'envoyé' else '⏳'
            status_color = (0, 0.6, 0, 1) if statut == 'envoyé' else (0.8, 0.5, 0, 1)
            
            # Carte
            card = RoundedCard(bg_color=(0.95, 0.95, 0.95, 1), size_hint_y=None, height=110)
            
            # Ligne 1: Type, date, statut
            line1 = BoxLayout(size_hint_y=None, height=30)
            line1.add_widget(Label(text=f"{type_icon} {comm_type.upper()}", font_size=14, bold=True, halign='left'))
            line1.add_widget(Label(text=date_formatted, font_size=11, color=(0.5, 0.5, 0.5, 1), halign='center'))
            line1.add_widget(Label(text=f"{status_icon} {statut}", font_size=11, color=status_color, halign='right'))
            card.add_widget(line1)
            
            # Ligne 2: Détails
            if details:
                details_text = details[:100] + "..." if len(details) > 100 else details
                line2 = Label(text=details_text, font_size=12, size_hint_y=None, height=35, halign='left')
                line2.bind(size=line2.setter('text_size'))
                card.add_widget(line2)
            
            # Ligne 3: Notes
            if notes:
                notes_text = f"📝 {notes[:80]}" + ("..." if len(notes) > 80 else "")
                line3 = Label(text=notes_text, font_size=11, color=(0.6, 0.4, 0.2, 1), size_hint_y=None, height=30, halign='left')
                line3.bind(size=line3.setter('text_size'))
                card.add_widget(line3)
            
            # Boutons d'action
            action_bar = BoxLayout(size_hint_y=None, height=35, spacing=5)
            
            copy_btn = Button(text="Copier", size_hint_x=0.33, font_size=11, background_color=(0.5, 0.5, 0.5, 1))
            copy_btn.bind(on_press=lambda x, d=details: self.copy_to_clipboard(d))
            action_bar.add_widget(copy_btn)
            
            if comm_type in ['appel', 'whatsapp']:
                recall_btn = Button(text="Rappeler", size_hint_x=0.33, font_size=11, background_color=(0.2, 0.6, 0.9, 1))
                recall_btn.bind(on_press=lambda x, cid=self.client_id: self.recall_client(cid))
                action_bar.add_widget(recall_btn)
            
            delete_btn = Button(text="Suppr", size_hint_x=0.33, font_size=11, background_color=(0.8, 0.3, 0.3, 1))
            delete_btn.bind(on_press=lambda x, hid=comm_id: self.delete_communication(hid))
            action_bar.add_widget(delete_btn)
            
            card.add_widget(action_bar)
            
            self.history_container.add_widget(card)
        
        # Ajuster la hauteur
        self.history_container.height = len(history) * 125
    
    def update_stats(self, history):
        """Met à jour les statistiques"""
        total = len(history)
        types = {}
        for h in history:
            t = h[2]
            types[t] = types.get(t, 0) + 1
        
        stats_text = f"📊 Total: {total} communications"
        for t, count in types.items():
            icon = self.get_type_icon(t)
            stats_text += f" | {icon} {t}: {count}"
        
        self.stats_label.text = stats_text
    
    def get_type_icon(self, comm_type):
        """Retourne l'icône pour le type de communication"""
        icons = {
            'appel': '📞',
            'whatsapp': '💬',
            'email': '📧',
            'rappel': '⏰',
            'note': '📝'
        }
        return icons.get(comm_type, '📌')
    
    def on_filter_change(self, instance, value):
        """Filtre changé"""
        self.load_history()
    
    def on_search(self, instance, value):
        """Recherche changée"""
        self.load_history()
    
    def copy_to_clipboard(self, text):
        """Copie le texte dans le presse-papier"""
        from kivy.core.clipboard import Clipboard
        Clipboard.copy(text)
        self.show_message("Copié", "Texte copié dans le presse-papier")
    
    def recall_client(self, client_id):
        """Rappelle le client"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT telephone FROM clients WHERE id = ?", (client_id,))
            result = cursor.fetchone()
            if result and result[0]:
                phone = result[0]
                import webbrowser
                webbrowser.open(f'tel:{phone}')
                self.show_message("Info", f"Appel vers {phone}")
            else:
                self.show_message("Erreur", "Numéro non disponible")
        except Exception as e:
            print(f"❌ Erreur rappel: {e}")
        finally:
            conn.close()
    
    def delete_communication(self, comm_id):
        """Supprime une communication"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(text="Supprimer cette communication ?", font_size=14))
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def do_delete(instance):
            popup.dismiss()
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM historique_communications WHERE id = ?", (comm_id,))
                conn.commit()
                self.load_history()
                self.show_message("Succès", "Communication supprimée")
            except Exception as e:
                print(f"❌ Erreur suppression: {e}")
            finally:
                conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        delete_btn = Button(text="SUPPRIMER", background_color=(0.8, 0.2, 0.2, 1))
        delete_btn.bind(on_press=do_delete)
        buttons.add_widget(delete_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="🗑️ Confirmation", content=content, size_hint=(0.7, 0.3))
        popup.open()
    
    def clear_all_history(self, instance):
        """Supprime tout l'historique du client"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(
            text=f"Supprimer TOUT l'historique de {self.client_name} ?\n\nCette action est irréversible.",
            font_size=14
        ))
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def do_clear(instance):
            popup.dismiss()
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM historique_communications WHERE client_id = ?", (self.client_id,))
                conn.commit()
                self.load_history()
                self.show_message("Succès", "Historique effacé")
            except Exception as e:
                print(f"❌ Erreur suppression: {e}")
            finally:
                conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        clear_btn = Button(text="EFFACER TOUT", background_color=(0.8, 0.2, 0.2, 1))
        clear_btn.bind(on_press=do_clear)
        buttons.add_widget(clear_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="⚠️ ATTENTION", content=content, size_hint=(0.8, 0.4))
        popup.open()
    
    def export_history(self, instance):
        """Exporte l'historique"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT date, type, details, notes, statut
                FROM historique_communications
                WHERE client_id = ?
                ORDER BY date DESC
            """, (self.client_id,))
            history = cursor.fetchall()
            
            if not history:
                self.show_message("Info", "Aucune donnée à exporter")
                return
            
            # Créer le contenu
            filename = f"historique_{self.client_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            
            content = f"HISTORIQUE DES COMMUNICATIONS - {self.client_name}\n"
            content += f"Date: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
            content += "=" * 50 + "\n\n"
            
            for h in history:
                date = h[0][:16] if h[0] else ''
                content += f"[{date}] {h[1].upper()}\n"
                if h[2]:
                    content += f"  Détails: {h[2]}\n"
                if h[3]:
                    content += f"  Notes: {h[3]}\n"
                content += f"  Statut: {h[4]}\n"
                content += "-" * 30 + "\n"
            
            # Sauvegarder
            from plyer import storagepath
            from os.path import join
            
            try:
                # Essayer d'enregistrer dans Documents
                docs_path = storagepath.get_documents_dir()
                filepath = join(docs_path, filename)
            except:
                # Fallback: dossier courant
                filepath = filename
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self.show_message("Succès", f"Exporté vers {filepath}")
            
        except Exception as e:
            print(f"❌ Erreur export: {e}")
            self.show_message("Erreur", str(e))
        finally:
            conn.close()
    
    def show_message(self, title, message):
        """Affiche un message temporaire"""
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 3)
    
    def go_back(self, instance):
        self.manager.current = 'client_detail'        
        


# ============================================================================
# LISTE HISTORIQUE DES ELTS
# ============================================================================        
        
class HistoryListItem(RecycleDataViewBehavior, BoxLayout):
    """Élément pour l'historique des communications"""
    text = StringProperty("")
    
    def refresh_view_attrs(self, rv, index, data):
        self.text = data.get('text', '')
        return super().refresh_view_attrs(rv, index, data)        
        

# ============================================================================
# ÉCRAN DETAILS CLIENT
# ============================================================================

class ClientDetailScreen(Screen):
    """Écran de détail d'un client avec actions"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client_id = None
        self.client_data = None
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.08), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.1, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='DÉTAIL CLIENT', font_size=18, bold=True))
        edit_btn = Button(text='EDIT', size_hint=(0.1, 1), font_size=14)
        edit_btn.bind(on_press=self.edit_client)
        header.add_widget(edit_btn)
        layout.add_widget(header)
        
        # ScrollView pour le contenu
        scroll = ScrollView()
        content = BoxLayout(orientation='vertical', padding=15, spacing=10, size_hint_y=None)
        content.bind(minimum_height=content.setter('height'))
        
        # Informations client
        self.info_frame = BoxLayout(orientation='vertical', spacing=8, size_hint_y=None)
        self.info_frame.bind(minimum_height=self.info_frame.setter('height'))
        content.add_widget(self.info_frame)
        
        # Séparateur
        content.add_widget(Widget(size_hint_y=None, height=10))
        
        # Actions
        actions_label = Label(text='ACTIONS', font_size=14, bold=True, size_hint_y=None, height=30)
        content.add_widget(actions_label)
        
        # Grille d'actions
        actions_grid = GridLayout(cols=2, spacing=10, size_hint_y=None, height=200)
        
        # Boutons d'action
        actions = [
            ('APPELER', self.call_client, (0.2, 0.6, 0.9, 1)),
            ('WHATSAPP', self.whatsapp_client, (0.3, 0.7, 0.4, 1)),
            ('EMAIL', self.email_client, (0.9, 0.5, 0.2, 1)),
            ('HISTORIQUE', self.show_history, (0.6, 0.3, 0.8, 1)),
            ('RAPPEL', self.add_rappel, (0.8, 0.4, 0.2, 1)),
            ('SUPPRIMER', self.delete_client, (0.8, 0.2, 0.2, 1))
        ]
        
        for text, callback, color in actions:
            btn = Button(text=text, background_color=color, font_size=14, bold=True)
            btn.bind(on_press=callback)
            actions_grid.add_widget(btn)
        
        content.add_widget(actions_grid)
        
        # Historique des communications
        history_label = Label(text='HISTORIQUE DES COMMUNICATIONS', font_size=14, bold=True, 
                              size_hint_y=None, height=30)
        content.add_widget(history_label)
        
        # ScrollView pour l'historique
        self.history_scroll = ScrollView(size_hint_y=None, height=200)
        self.history_container = BoxLayout(orientation='vertical', size_hint_y=None, spacing=5)
        self.history_container.bind(minimum_height=self.history_container.setter('height'))
        self.history_scroll.add_widget(self.history_container)
        content.add_widget(self.history_scroll)
        
        scroll.add_widget(content)
        layout.add_widget(scroll)
        
        self.add_widget(layout)
    
    def set_client(self, client_id, client_data):
        """Définit le client à afficher"""
        self.client_id = client_id
        self.client_data = client_data
        self.load_client_info()
        self.load_history()
    
    def load_client_info(self):
        """Charge et affiche les infos du client"""
        self.info_frame.clear_widgets()
        
        if not self.client_data:
            return
        
        # Utiliser RoundedCard au lieu de BoxLayout manuel
        card = RoundedCard(bg_color=(0.2, 0.6, 0.8, 0.1), size_hint_y=None, height=180)
        
        # Nom
        nom_label = Label(text=f"{self.client_data[1]}", font_size=18, bold=True, 
                          size_hint_y=None, height=35, halign='left')
        nom_label.bind(size=nom_label.setter('text_size'))
        card.add_widget(nom_label)
        
        # Email
        email = self.client_data[2] if self.client_data[2] else 'Non renseigné'
        email_label = Label(text=f"{email}", font_size=14, size_hint_y=None, height=30, halign='left')
        email_label.bind(size=email_label.setter('text_size'))
        card.add_widget(email_label)
        
        # Téléphone
        tel = self.client_data[3] if self.client_data[3] else 'Non renseigné'
        tel_label = Label(text=f"{tel}", font_size=14, size_hint_y=None, height=30, halign='left')
        tel_label.bind(size=tel_label.setter('text_size'))
        card.add_widget(tel_label)
        
        # Adresse
        adresse = self.client_data[4] if len(self.client_data) > 4 and self.client_data[4] else 'Non renseignée'
        adresse_label = Label(text=f"{adresse}", font_size=12, size_hint_y=None, height=30, halign='left')
        adresse_label.bind(size=adresse_label.setter('text_size'))
        card.add_widget(adresse_label)
        
        # Ville et Pays
        ville = self.client_data[5] if len(self.client_data) > 5 and self.client_data[5] else ''
        pays = self.client_data[6] if len(self.client_data) > 6 and self.client_data[6] else ''
        location = f"{ville} {pays}".strip() if ville or pays else 'Non renseigné'
        loc_label = Label(text=f"{location}", font_size=12, size_hint_y=None, height=30, halign='left')
        loc_label.bind(size=loc_label.setter('text_size'))
        card.add_widget(loc_label)
        
        self.info_frame.add_widget(card)
    
    
    def load_history(self):
        """Charge l'historique des communications"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT date, type, details, notes, statut
                FROM historique_communications
                WHERE client_id = ?
                ORDER BY date DESC
                LIMIT 20
            """, (self.client_id,))
            
            history = cursor.fetchall()
            
            # Vider le conteneur
            self.history_container.clear_widgets()
            
            if not history:
                empty_label = Label(
                    text="Aucune communication enregistrée",
                    font_size=12,
                    color=(0.5, 0.5, 0.5, 1),
                    size_hint_y=None,
                    height=50,
                    halign='center'
                )
                self.history_container.add_widget(empty_label)
                self.history_container.height = 50
                return
            
            for h in history:
                date = h[0][:16] if h[0] else ''
                type_icon = self.get_type_icon(h[1])
                status_icon = '✅' if h[4] == 'envoyé' else '⏳'
                
                # Utiliser RoundedCard
                history_card = RoundedCard(bg_color=(0.95, 0.95, 0.95, 1), size_hint_y=None, height=80)
                
                # Ligne 1: Type et date
                line1 = BoxLayout(size_hint_y=None, height=25)
                line1.add_widget(Label(text=f"{type_icon} {h[1]}", font_size=12, bold=True, halign='left'))
                line1.add_widget(Label(text=date, font_size=10, color=(0.5, 0.5, 0.5, 1), halign='right'))
                history_card.add_widget(line1)
                
                # Ligne 2: Détails
                if h[2]:
                    line2 = Label(text=f"{status_icon} {h[2][:60]}", font_size=11, size_hint_y=None, height=25, halign='left')
                    line2.bind(size=line2.setter('text_size'))
                    history_card.add_widget(line2)
                
                # Ligne 3: Notes
                if h[3]:
                    line3 = Label(text=f"📝 {h[3][:60]}", font_size=10, color=(0.6, 0.4, 0.2, 1), size_hint_y=None, height=25, halign='left')
                    line3.bind(size=line3.setter('text_size'))
                    history_card.add_widget(line3)
                
                self.history_container.add_widget(history_card)
            
            # Ajuster la hauteur
            self.history_container.height = len(history) * 85
            
        except Exception as e:
            print(f"❌ Erreur load_history: {e}")
        finally:
            conn.close()
    
    def get_type_icon(self, comm_type):
        """Retourne l'icône pour le type de communication"""
        icons = {
            'appel': '📞',
            'whatsapp': '💬',
            'email': '📧',
            'rappel': '⏰',
            'note': '📝'
        }
        return icons.get(comm_type, '📌')
    
    def call_client(self, instance):
        """Appelle le client"""
        if not self.client_data or not self.client_data[3]:
            self.show_message("Erreur", "Numéro de téléphone non disponible")
            return
        
        phone = self.client_data[3]
        self.show_phone_dialog(phone, 'appel')
    
    def whatsapp_client(self, instance):
        """Envoie un message WhatsApp"""
        if not self.client_data or not self.client_data[3]:
            self.show_message("Erreur", "Numéro de téléphone non disponible")
            return
        
        phone = self.client_data[3]
        self.show_whatsapp_dialog(phone)
    
    def email_client(self, instance):
        """Envoie un email"""
        if not self.client_data or not self.client_data[2]:
            self.show_message("Erreur", "Email non disponible")
            return
        
        email = self.client_data[2]
        self.show_email_dialog(email)
    
    def show_phone_dialog(self, phone, action_type):
        """Affiche la boîte de dialogue pour l'appel"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        content.add_widget(Label(text=f"Numéro: {phone}", font_size=14))
        
        message_input = TextInput(
            hint_text="Notes (optionnel)",
            multiline=True,
            size_hint_y=None,
            height=80
        )
        content.add_widget(message_input)
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def call(instance):
            notes = message_input.text
            popup.dismiss()
            
            # Enregistrer dans l'historique
            app = App.get_running_app()
            app.db.add_communication(
                self.client_id, action_type, f"Appel au {phone}", notes
            )
            
            # Ouvrir l'application d'appel
            import webbrowser
            webbrowser.open(f'tel:{phone}')
            
            self.show_message("Succès", f"Appel lancé vers {phone}")
            self.load_history()
        
        def cancel(instance):
            popup.dismiss()
        
        call_btn = Button(text="APPELER", background_color=(0.2, 0.7, 0.3, 1))
        call_btn.bind(on_press=call)
        buttons.add_widget(call_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title=f"📞 {action_type.upper()}", content=content, size_hint=(0.9, 0.5))
        popup.open()
    
    def show_whatsapp_dialog(self, phone):
        """Affiche la boîte de dialogue pour WhatsApp"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        # Nettoyer le numéro
        phone_clean = re.sub(r'[^0-9+]', '', str(phone))
        if not phone_clean.startswith('+') and not phone_clean.startswith('00'):
            phone_clean = f'+257{phone_clean}'  # Indicatif Burundi
        
        content.add_widget(Label(text=f"Numéro WhatsApp: {phone_clean}", font_size=12))
        
        message_input = TextInput(
            hint_text="Message à envoyer",
            multiline=True,
            size_hint_y=None,
            height=100,
            text="Bonjour, nous vous contactons de Facturos. Comment puis-je vous aider ?"
        )
        content.add_widget(message_input)
        
        notes_input = TextInput(
            hint_text="Notes (optionnel)",
            multiline=True,
            size_hint_y=None,
            height=60
        )
        content.add_widget(notes_input)
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def send_whatsapp(instance):
            message = message_input.text
            notes = notes_input.text
            popup.dismiss()
            
            # Enregistrer dans l'historique
            app = App.get_running_app()
            app.db.add_communication(
                self.client_id, 'whatsapp', f"Message envoyé: {message[:100]}", notes
            )
            
            # Ouvrir WhatsApp
            import urllib.parse
            encoded_msg = urllib.parse.quote(message)
            whatsapp_url = f"https://wa.me/{phone_clean}?text={encoded_msg}"
            
            import webbrowser
            webbrowser.open(whatsapp_url)
            
            self.show_message("Succès", "WhatsApp ouvert. Envoyez votre message.")
            self.load_history()
        
        def cancel(instance):
            popup.dismiss()
        
        send_btn = Button(text="ENVOYER", background_color=(0.2, 0.7, 0.3, 1))
        send_btn.bind(on_press=send_whatsapp)
        buttons.add_widget(send_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="ENVOYER WHATSAPP", content=content, size_hint=(0.9, 0.7))
        popup.open()
    
    def show_email_dialog(self, email):
        """Affiche la boîte de dialogue pour l'email"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        content.add_widget(Label(text=f"Email: {email}", font_size=12))
        
        sujet_input = TextInput(
            hint_text="Sujet",
            multiline=False,
            size_hint_y=None,
            height=40,
            text="Facturos - Information importante"
        )
        content.add_widget(sujet_input)
        
        message_input = TextInput(
            hint_text="Message",
            multiline=True,
            size_hint_y=None,
            height=150,
            text="Bonjour,\n\nNous vous contactons concernant votre compte Facturos.\n\nCordialement."
        )
        content.add_widget(message_input)
        
        notes_input = TextInput(
            hint_text="Notes (optionnel)",
            multiline=True,
            size_hint_y=None,
            height=60
        )
        content.add_widget(notes_input)
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def send_email(instance):
            sujet = sujet_input.text
            message = message_input.text
            notes = notes_input.text
            popup.dismiss()
            
            # Enregistrer dans l'historique
            app = App.get_running_app()
            app.db.add_communication(
                self.client_id, 'email', f"Email envoyé: {sujet}", notes
            )
            
            # Ouvrir l'application email
            import webbrowser
            import urllib.parse
            mailto = f"mailto:{email}?subject={urllib.parse.quote(sujet)}&body={urllib.parse.quote(message)}"
            webbrowser.open(mailto)
            
            self.show_message("Succès", "Client email ouvert. Envoyez votre message.")
            self.load_history()
        
        def cancel(instance):
            popup.dismiss()
        
        send_btn = Button(text="ENVOYER", background_color=(0.2, 0.7, 0.3, 1))
        send_btn.bind(on_press=send_email)
        buttons.add_widget(send_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="ENVOYER EMAIL", content=content, size_hint=(0.9, 0.8))
        popup.open()
    
    def add_rappel(self, instance):
        """Ajoute un rappel pour le client"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        # Date du rappel
        content.add_widget(Label(text="Date du rappel:", font_size=12))
        date_input = TextInput(
            hint_text="JJ/MM/AAAA",
            multiline=False,
            size_hint_y=None,
            height=40,
            text=datetime.now().strftime("%d/%m/%Y")
        )
        content.add_widget(date_input)
        
        # Type de rappel
        content.add_widget(Label(text="Type:", font_size=12))
        type_spinner = Spinner(
            text='Appel',
            values=('Appel', 'WhatsApp', 'Email', 'Visite', 'Relance', 'Autre'),
            size_hint=(1, None),
            height=40
        )
        content.add_widget(type_spinner)
        
        # Priorité
        content.add_widget(Label(text="Priorité:", font_size=12))
        priorite_spinner = Spinner(
            text='Moyenne',
            values=('Basse', 'Moyenne', 'Haute', 'Urgente'),
            size_hint=(1, None),
            height=40
        )
        content.add_widget(priorite_spinner)
        
        # Notes
        content.add_widget(Label(text="Notes:", font_size=12))
        notes_input = TextInput(
            hint_text="Détails du rappel...",
            multiline=True,
            size_hint_y=None,
            height=80
        )
        content.add_widget(notes_input)
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def save_rappel(instance):
            date_rappel = date_input.text
            type_rappel = type_spinner.text
            priorite = priorite_spinner.text
            notes = notes_input.text
            popup.dismiss()
            
            # Enregistrer dans la base
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            try:
                # Convertir la date
                date_obj = datetime.strptime(date_rappel, "%d/%m/%Y")
                date_formatted = date_obj.strftime("%Y-%m-%d")
                
                cursor.execute('''
                    INSERT INTO rappels (client_id, date_rappel, type, priorite, notes, statut, cree_par)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (self.client_id, date_formatted, type_rappel, priorite, notes, 'Planifié', 'Mobile'))
                conn.commit()
                
                # Enregistrer dans l'historique des communications
                app.db.add_communication(
                    self.client_id, 'rappel', f"Rappel {type_rappel} planifié le {date_rappel}", notes
                )
                
                self.show_message("Succès", f"Rappel {type_rappel} planifié pour le {date_rappel}")
                self.load_history()
                
            except Exception as e:
                print(f"❌ Erreur rappel: {e}")
                self.show_message("Erreur", "Date invalide. Utilisez JJ/MM/AAAA")
            finally:
                conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        save_btn = Button(text="ENREGISTRER", background_color=(0.2, 0.7, 0.3, 1))
        save_btn.bind(on_press=save_rappel)
        buttons.add_widget(save_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="AJOUTER UN RAPPEL", content=content, size_hint=(0.9, 0.8))
        popup.open()
    
    def show_history(self, instance):
        """Affiche l'historique complet des communications du client"""
        if self.client_id is None:
            self.show_message("Erreur", "Aucun client sélectionné")
            return
        
        # Récupérer l'historique depuis la base
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT id, date, type, details, notes, statut
                FROM historique_communications
                WHERE client_id = ?
                ORDER BY date DESC
                LIMIT 100
            """, (self.client_id,))
            
            history = cursor.fetchall()
            
            if not history:
                self.show_message("Info", "Aucune communication enregistrée")
                return
            
            # Créer une popup avec l'historique
            content = BoxLayout(orientation='vertical', padding=10, spacing=10)
            
            # Titre
            title_label = Label(
                text=f"HISTORIQUE - {self.client_data[1] if self.client_data else 'Client'}",
                font_size=16,
                bold=True,
                size_hint_y=None,
                height=40
            )
            content.add_widget(title_label)
            
            # ScrollView pour la liste
            scroll = ScrollView()
            history_container = BoxLayout(orientation='vertical', size_hint_y=None, spacing=5)
            history_container.bind(minimum_height=history_container.setter('height'))
            
            # Afficher chaque communication en utilisant le format existant
            for h in history:
                comm_id, date, comm_type, details, notes, statut = h
                
                # Formater la date
                try:
                    date_obj = datetime.strptime(date, "%Y-%m-%d %H:%M:%S")
                    date_formatted = date_obj.strftime("%d/%m/%Y à %H:%M")
                except:
                    date_formatted = date[:16] if date else ''
                
                type_icon = self.get_type_icon(comm_type)
                status_icon = '✅' if statut == 'envoyé' else '⏳'
                status_color = (0, 0.6, 0, 1) if statut == 'envoyé' else (0.8, 0.5, 0, 1)
                
                # Carte
                card = RoundedCard(bg_color=(0.95, 0.95, 0.95, 1), size_hint_y=None, height=110)
                
                # Ligne 1: Type, date, statut
                line1 = BoxLayout(size_hint_y=None, height=30)
                line1.add_widget(Label(text=f"{type_icon} {comm_type.upper()}", font_size=14, bold=True, halign='left'))
                line1.add_widget(Label(text=date_formatted, font_size=11, color=(0.5, 0.5, 0.5, 1), halign='center'))
                line1.add_widget(Label(text=f"{status_icon} {statut}", font_size=11, color=status_color, halign='right'))
                card.add_widget(line1)
                
                # Ligne 2: Détails
                if details:
                    details_text = details[:100] + "..." if len(details) > 100 else details
                    line2 = Label(text=details_text, font_size=12, size_hint_y=None, height=35, halign='left')
                    line2.bind(size=line2.setter('text_size'))
                    card.add_widget(line2)
                
                # Ligne 3: Notes
                if notes:
                    notes_text = f"📝 {notes[:80]}" + ("..." if len(notes) > 80 else "")
                    line3 = Label(text=notes_text, font_size=11, color=(0.6, 0.4, 0.2, 1), size_hint_y=None, height=30, halign='left')
                    line3.bind(size=line3.setter('text_size'))
                    card.add_widget(line3)
                
                history_container.add_widget(card)
            
            # Ajuster la hauteur
            history_container.height = len(history) * 125
            scroll.add_widget(history_container)
            content.add_widget(scroll)
            
            # Bouton fermer
            close_btn = Button(text="FERMER", size_hint_y=None, height=50, background_color=(0.3, 0.3, 0.3, 1))
            close_btn.bind(on_press=lambda x: popup.dismiss())
            content.add_widget(close_btn)
            
            popup = Popup(
                title=f"Historique des communications",
                content=content,
                size_hint=(0.9, 0.85)
            )
            popup.open()
            
        except Exception as e:
            print(f"❌ Erreur chargement historique: {e}")
            self.show_message("Erreur", f"Erreur: {str(e)[:50]}")
        finally:
            conn.close()
    
    def delete_client(self, instance):
        """Supprime le client après vérification des factures associées"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            # Vérifier si le client a des factures
            cursor.execute("SELECT COUNT(*) FROM factures WHERE client_id = ?", (self.client_id,))
            facture_count = cursor.fetchone()[0]
            
            # Récupérer quelques factures pour info
            factures_info = []
            if facture_count > 0:
                cursor.execute("""
                    SELECT numero, date, total_ttc
                    FROM factures
                    WHERE client_id = ?
                    ORDER BY date DESC
                    LIMIT 3
                """, (self.client_id,))
                factures_info = cursor.fetchall()
            
            # Créer le contenu du message
            content = BoxLayout(orientation='vertical', padding=10, spacing=10)
            
            if facture_count > 0:
                # Client avec factures - ne pas autoriser la suppression
                message = f"❌ Impossible de supprimer {self.client_data[1]}\n\n"
                message += f"Ce client est associé à {facture_count} facture(s) :\n\n"
                for f in factures_info:
                    message += f"• {f[0]} du {f[1][:10]} - {f[2]:,.0f} Fbu\n"
                message += f"\nPour des raisons d'intégrité des données, la suppression n'est pas autorisée."
                message += f"\n\n✓ Le client peut être désactivé (option ci-dessous)"
                
                content.add_widget(Label(text=message, font_size=12, size_hint_y=None, height=200))
                
                buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
                
                # Bouton désactiver
                desactiver_btn = Button(text="DÉSACTIVER", background_color=(0.8, 0.5, 0.2, 1))
                desactiver_btn.bind(on_press=lambda x: self.desactivate_client(popup))
                buttons.add_widget(desactiver_btn)
                
                # Bouton annuler
                annuler_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
                annuler_btn.bind(on_press=lambda x: popup.dismiss())
                buttons.add_widget(annuler_btn)
                
                content.add_widget(buttons)
                popup = Popup(title="❌ SUPPRESSION IMPOSSIBLE", content=content, size_hint=(0.9, 0.6))
                popup.open()
                
            else:
                # Client sans facture - autoriser la suppression
                message = f"Supprimer {self.client_data[1]} ?\n\n"
                message += "Cette action est irréversible.\n"
                message += "Le client sera définitivement supprimé de la base."
                
                content.add_widget(Label(text=message, font_size=14))
                
                buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
                
                def do_delete(instance):
                    popup.dismiss()
                    self.perform_client_delete()
                    
                    # ⭐⭐⭐ AJOUTER LE LOG SUPPRESSION ⭐⭐⭐
                    try:
                        app.db.add_log(
                            app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                            'client_suppression',
                            'Clients',
                            f"Suppression client: {self.client_data[1]} (ID: {self.client_id})"
                        )
                    except Exception as e:
                        print(f"⚠️ Erreur log: {e}")
                    
                    self.show_message("Succès", "Client supprimé")
                
                def cancel(instance):
                    popup.dismiss()
                
                delete_btn = Button(text="SUPPRIMER", background_color=(0.8, 0.2, 0.2, 1))
                delete_btn.bind(on_press=do_delete)
                buttons.add_widget(delete_btn)
                
                cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
                cancel_btn.bind(on_press=cancel)
                buttons.add_widget(cancel_btn)
                
                content.add_widget(buttons)
                popup = Popup(title="⚠ CONFIRMER SUPPRESSION", content=content, size_hint=(0.8, 0.5))
                popup.open()
            
        except Exception as e:
            print(f"❌ Erreur vérification client: {e}")
            self.show_message("Erreur", str(e))
        finally:
            conn.close()

    def desactivate_client(self, parent_popup):
        """Désactive un client (alternative à la suppression)"""
        parent_popup.dismiss()
        
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(
            text=f"Désactiver {self.client_data[1]} ?\n\n"
                 f"Le client ne sera plus visible dans la liste,\n"
                 f"mais restera dans l'historique des factures.\n\n"
                 f"Vous pourrez le réactiver plus tard si nécessaire.",
            font_size=14
        ))
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def do_desactivate(instance):
            popup.dismiss()
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("UPDATE clients SET statut = 'inactif' WHERE id = ?", (self.client_id,))
                conn.commit()
                self.show_message("Succès", f"Client {self.client_data[1]} désactivé")
                self.manager.current = 'clients'
                self.manager.get_screen('clients').load_clients()
            except Exception as e:
                print(f"❌ Erreur désactivation: {e}")
                self.show_message("Erreur", str(e))
            finally:
                conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        desactiver_btn = Button(text="DÉSACTIVER", background_color=(0.8, 0.5, 0.2, 1))
        desactiver_btn.bind(on_press=do_desactivate)
        buttons.add_widget(desactiver_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="🔘 DÉSACTIVER LE CLIENT", content=content, size_hint=(0.8, 0.5))
        popup.open()

    def perform_client_delete(self):
        """Exécute la suppression du client"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("DELETE FROM clients WHERE id = ?", (self.client_id,))
            conn.commit()
            self.show_message("Succès", f"Client {self.client_data[1]} supprimé")
            self.manager.current = 'clients'
            self.manager.get_screen('clients').load_clients()
            
            # Synchroniser la suppression avec le serveur
            if app.network and app.network.connected:
                delete_data = {
                    'id': self.client_id,
                    'nom': self.client_data[1],
                    'action': 'delete'
                }
                app.network.send_update('clients', 'delete', delete_data)
            
        except Exception as e:
            print(f"❌ Erreur suppression: {e}")
            self.show_message("Erreur", str(e))
        finally:
            conn.close()
    
    def edit_client(self, instance):
        """Ouvre le formulaire d'édition"""
        self.manager.current = 'client_form'
        self.manager.get_screen('client_form').set_mode('edit', self.client_id)
        self.show_message("Succès", "Client modifié avec succès")        
        
    
    def show_message(self, title, message):
        """Affiche un message temporaire"""
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 3)
    
    def go_back(self, instance):
        self.manager.current = 'clients'


# ============================================================================
# ÉCRAN CLIENTS
# ============================================================================

class ClientFormScreen(Screen):
    """Formulaire d'ajout/modification de client - Version améliorée"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mode = 'add'
        self.client_id = None
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
        
        # En-tête
        header = BoxLayout(size_hint_y=0.1)
        back_btn = Button(text='GO BACK', size_hint_x=0.15, background_color=(0.5, 0.5, 0.5, 1))
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        self.title_label = Label(text="", font_size=18, bold=True)
        header.add_widget(self.title_label)
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Champs du formulaire
        scroll = ScrollView()
        form = BoxLayout(orientation='vertical', size_hint_y=None, spacing=10)
        form.bind(minimum_height=form.setter('height'))
        
        self.nom_input = TextInput(hint_text="Nom du client *", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.nom_input)
        
        self.email_input = TextInput(hint_text="Email", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.email_input)
        
        self.tel_input = TextInput(hint_text="Téléphone", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.tel_input)
        
        self.adresse_input = TextInput(hint_text="Adresse", multiline=True, size_hint_y=None, height=80)
        form.add_widget(self.adresse_input)
        
        self.ville_input = TextInput(hint_text="Ville", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.ville_input)
        
        self.pays_input = TextInput(hint_text="Pays", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.pays_input)
        
        self.type_input = Spinner(text="Particulier", values=["Particulier", "Professionnel", "Entreprise"], 
                                  size_hint_y=None, height=50)
        form.add_widget(self.type_input)
        
        self.notes_input = TextInput(hint_text="Notes", multiline=True, size_hint_y=None, height=80)
        form.add_widget(self.notes_input)
        
        scroll.add_widget(form)
        layout.add_widget(scroll)
        
        # Boutons
        buttons = BoxLayout(size_hint_y=None, height=60, spacing=10)
        save_btn = Button(text="ENREGISTRER", background_color=(0.2, 0.7, 0.3, 1))
        save_btn.bind(on_press=self.save_client)
        buttons.add_widget(save_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=self.go_back)
        buttons.add_widget(cancel_btn)
        
        layout.add_widget(buttons)
        
        self.add_widget(layout)
    
    def set_mode(self, mode, client_id=None):
        """Définit le mode (add/edit)"""
        self.mode = mode
        self.client_id = client_id
        
        if mode == 'add':
            self.title_label.text = "NOUVEAU CLIENT"
            self.clear_form()
        else:
            self.title_label.text = "MODIFIER CLIENT"
            self.load_client(client_id)
    
    def clear_form(self):
        """Efface le formulaire"""
        self.nom_input.text = ""
        self.email_input.text = ""
        self.tel_input.text = ""
        self.adresse_input.text = ""
        self.ville_input.text = ""
        self.pays_input.text = ""
        self.type_input.text = "Particulier"
        self.notes_input.text = ""
    
    def load_client(self, client_id):
        """Charge les données du client"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT nom, email, telephone, adresse, ville, pays, type_client, notes
                FROM clients WHERE id = ?
            """, (client_id,))
            client = cursor.fetchone()
            
            if client:
                self.nom_input.text = client[0] or ""
                self.email_input.text = client[1] or ""
                self.tel_input.text = client[2] or ""
                self.adresse_input.text = client[3] or ""
                self.ville_input.text = client[4] or ""
                self.pays_input.text = client[5] or ""
                self.type_input.text = client[6] or "Particulier"
                self.notes_input.text = client[7] or ""
        except Exception as e:
            print(f"❌ Erreur load_client: {e}")
        finally:
            conn.close()
    
    def save_client(self, instance):
        """Enregistre le client"""
        nom = self.nom_input.text.strip()
        if not nom:
            self.show_message("Erreur", "Le nom du client est obligatoire")
            return
        
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            if self.mode == 'add':
                client_uuid = str(uuid.uuid4())
                cursor.execute('''
                    INSERT INTO clients 
                    (nom, email, telephone, adresse, ville, pays, type_client, notes, created_at, uuid, statut)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (nom, self.email_input.text, self.tel_input.text, 
                      self.adresse_input.text, self.ville_input.text, self.pays_input.text,
                      self.type_input.text, self.notes_input.text,
                      datetime.now().isoformat(), client_uuid, 'actif'))
                
                client_id = cursor.lastrowid
                message = "Client ajouté avec succès"
                
                print(f"✅ Client ajouté localement: {nom} (ID: {client_id}, UUID: {client_uuid})")
                
                # ⭐⭐⭐ AJOUTER LE LOG CLIENT (AJOUT) ⭐⭐⭐
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'client_ajout',
                        'Clients',
                        f"Ajout client: {nom} (Tél: {self.tel_input.text})"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log: {e}")
                
                # Synchronisation
                if app.network and app.network.connected:
                    client_data = {
                        'nom': nom,
                        'email': self.email_input.text,
                        'telephone': self.tel_input.text,
                        'adresse': self.adresse_input.text,
                        'ville': self.ville_input.text,
                        'pays': self.pays_input.text,
                        'type_client': self.type_input.text,
                        'notes': self.notes_input.text,
                        'created_at': datetime.now().isoformat(),
                        'uuid': client_uuid
                    }
                    print(f"📤 Envoi du client au serveur: {nom}")
                    app.network.send_update('clients', 'insert', client_data)
                else:
                    print(f"⚠️ Non connecté au serveur, client {nom} non synchronisé")
            
            else:  # mode edit
                cursor.execute('''
                    UPDATE clients SET 
                        nom = ?, email = ?, telephone = ?, adresse = ?, ville = ?,
                        pays = ?, type_client = ?, notes = ?
                    WHERE id = ?
                ''', (nom, self.email_input.text, self.tel_input.text,
                      self.adresse_input.text, self.ville_input.text, self.pays_input.text,
                      self.type_input.text, self.notes_input.text, self.client_id))
                message = "Client modifié avec succès"
                
                print(f"✏️ Client modifié localement: {nom} (ID: {self.client_id})")
                
                # ⭐⭐⭐ AJOUTER LE LOG CLIENT (MODIFICATION) ⭐⭐⭐
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'client_modification',
                        'Clients',
                        f"Modification client: {nom} (ID: {self.client_id})"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log: {e}")
                
                # Synchronisation
                if app.network and app.network.connected:
                    client_data = {
                        'id': self.client_id,
                        'nom': nom,
                        'email': self.email_input.text,
                        'telephone': self.tel_input.text,
                        'adresse': self.adresse_input.text,
                        'ville': self.ville_input.text,
                        'pays': self.pays_input.text,
                        'type_client': self.type_input.text,
                        'notes': self.notes_input.text
                    }
                    print(f"📤 Envoi de la modification au serveur: {nom}")
                    app.network.send_update('clients', 'update', client_data)
            
            conn.commit()
            self.show_message("Succès", message)
            
            # Rafraîchir la liste des clients
            self.manager.get_screen('clients').load_clients()
            self.go_back(None)
            
        except Exception as e:
            print(f"❌ Erreur save_client: {e}")
            import traceback
            traceback.print_exc()
            self.show_message("Erreur", f"Erreur: {str(e)[:50]}")
            conn.rollback()
        finally:
            conn.close()
    
    def show_message(self, title, message):
        """Affiche un message temporaire"""
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.8, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 2)
    
    def go_back(self, instance):
        self.manager.current = 'clients'



# ============================================================================
# ÉCRAN PRODUITS
# ============================================================================

class ProduitsScreen(Screen):
    """Écran de gestion des produits avec filtres avancés"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.all_products = []
        self.categories_list = []
        self.filters_visible = False
        self.is_initialized = False  # ⭐ AJOUTER CETTE LIGNE
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='CATALOGUE PRODUITS', font_size=18, bold=True))
        
        self.add_btn = Button(
            text='+ AJOUTER', 
            size_hint=(0.2, 1), 
            background_color=(0.2, 0.7, 0.3, 1),
            font_size=14,
            bold=True
        )
        self.add_btn.bind(on_release=self.open_add_product)
        header.add_widget(self.add_btn)
        
        layout.add_widget(header)
        
        # Barre de recherche
        search_layout = BoxLayout(size_hint=(1, 0.06), padding=5, spacing=5)
        search_layout.add_widget(Label(text='', size_hint_x=0.1, font_size=16))
        self.search_input = TextInput(
            hint_text='Rechercher...',
            multiline=False,
            size_hint_x=0.9,
            font_size=13,
            height=35,
            background_color=(0.95, 0.95, 0.95, 1)
        )
        self.search_input.bind(text=self.on_search)
        search_layout.add_widget(self.search_input)
        layout.add_widget(search_layout)
        
        # Bouton FILTRES
        self.filters_visible = False
        self.filter_btn = Button(
            text='FILTRES AVANCES', 
            size_hint_y=None, 
            height=40,
            background_color=(0.3, 0.3, 0.4, 1)
        )
        self.filter_btn.bind(on_press=self.toggle_filters)
        layout.add_widget(self.filter_btn)
        
        # ⭐ CRÉER LE PANEL MAIS NE PAS L'AJOUTER
        self.create_filters_panel()
        self.filters_panel_added = False
        
        # Liste des produits
        self.scroll = ScrollView()
        self.list_layout = BoxLayout(orientation='vertical', spacing=8, padding=10, size_hint_y=None)
        self.list_layout.bind(minimum_height=self.list_layout.setter('height'))
        self.scroll.add_widget(self.list_layout)
        layout.add_widget(self.scroll)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('NOUVEAU', 'nouvelle_vente'),
            ('PRODUITS', 'produits')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12, bold=True)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        
        self.add_widget(layout)
        
        
    def create_filters_panel(self):
        """Crée le panneau des filtres"""
        self.filters_panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=5, padding=5)
        self.filters_panel.height = 260
        self.filters_panel.opacity = 1
        self.filters_panel.disabled = False
        
        # Catégorie
        cat_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        cat_layout.add_widget(Label(text="Catégorie:", size_hint_x=0.3, font_size=12))
        self.cat_spinner = Spinner(
            text='Toutes',
            values=['Toutes'],
            size_hint_x=0.7,
            height=35
        )
        self.cat_spinner.bind(text=self.apply_filters)
        cat_layout.add_widget(self.cat_spinner)
        self.filters_panel.add_widget(cat_layout)
        
        # Statut
        status_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        status_layout.add_widget(Label(text="Statut:", size_hint_x=0.3, font_size=12))
        self.status_spinner = Spinner(
            text='Tous',
            values=['Tous', 'Actif', 'Inactif', 'Rupture', 'Alerte'],
            size_hint_x=0.7,
            height=35
        )
        self.status_spinner.bind(text=self.apply_filters)
        status_layout.add_widget(self.status_spinner)
        self.filters_panel.add_widget(status_layout)
        
        # Prix min/max
        price_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        price_layout.add_widget(Label(text="Prix min:", size_hint_x=0.3, font_size=12))
        self.prix_min_input = TextInput(text='', multiline=False, size_hint_x=0.3, height=35, input_filter='float')
        self.prix_min_input.bind(text=self.apply_filters)
        price_layout.add_widget(self.prix_min_input)
        
        price_layout.add_widget(Label(text="Prix max:", size_hint_x=0.2, font_size=12))
        self.prix_max_input = TextInput(text='', multiline=False, size_hint_x=0.3, height=35, input_filter='float')
        self.prix_max_input.bind(text=self.apply_filters)
        price_layout.add_widget(self.prix_max_input)
        self.filters_panel.add_widget(price_layout)
        
        # Stock min/max
        stock_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        stock_layout.add_widget(Label(text="Stock min:", size_hint_x=0.3, font_size=12))
        self.stock_min_input = TextInput(text='', multiline=False, size_hint_x=0.3, height=35, input_filter='int')
        self.stock_min_input.bind(text=self.apply_filters)
        stock_layout.add_widget(self.stock_min_input)
        
        stock_layout.add_widget(Label(text="Stock max:", size_hint_x=0.2, font_size=12))
        self.stock_max_input = TextInput(text='', multiline=False, size_hint_x=0.3, height=35, input_filter='int')
        self.stock_max_input.bind(text=self.apply_filters)
        stock_layout.add_widget(self.stock_max_input)
        self.filters_panel.add_widget(stock_layout)
        
        # Boutons
        btn_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        reset_btn = Button(text="Reinitialiser", size_hint_x=0.5, background_color=(0.8, 0.3, 0.3, 1))
        reset_btn.bind(on_press=self.reset_filters)
        btn_layout.add_widget(reset_btn)
        
        apply_btn = Button(text="Appliquer", size_hint_x=0.5, background_color=(0.2, 0.7, 0.3, 1))
        apply_btn.bind(on_press=self.apply_filters)
        btn_layout.add_widget(apply_btn)
        self.filters_panel.add_widget(btn_layout)

    def toggle_filters(self, instance):
        """Affiche/masque les filtres avancés - Version avec ajout/suppression"""
        print("🔍 toggle_filters appelé")
        
        # Récupérer le layout principal (le premier enfant de l'écran)
        layout = self.children[0]
        
        if self.filters_visible:
            # ⭐ SUPPRIMER LE PANEL
            if hasattr(self, 'filters_panel_added') and self.filters_panel_added:
                layout.remove_widget(self.filters_panel)
                self.filters_panel_added = False
            self.filter_btn.text = 'FILTRES AVANCES'
            self.filters_visible = False
            print("✅ Filtres supprimés")
        else:
            # ⭐ AJOUTER LE PANEL (après le bouton FILTRES)
            # Trouver l'index du bouton filtre
            filter_btn_index = None
            for i, child in enumerate(layout.children):
                if child == self.filter_btn:
                    filter_btn_index = i
                    break
            
            if filter_btn_index is not None:
                # Ajouter le panel après le bouton filtre (index + 1 car l'ordre est inversé)
                layout.add_widget(self.filters_panel, index=filter_btn_index)
                self.filters_panel_added = True
            else:
                # Fallback: ajouter à la fin
                layout.add_widget(self.filters_panel)
                self.filters_panel_added = True
            
            self.filter_btn.text = 'MASQUER FILTRES'
            self.filters_visible = True
            print("✅ Filtres ajoutés")
        
        
        
    def on_enter(self):
        """Charge les catégories et les produits UNIQUEMENT si non initialisé"""
        print("📱 ProduitsScreen affiché")
        
        # ⭐ ÉVITER LE RECHARGEMENT MULTIPLE
        if not self.is_initialized:
            self.load_categories()
            self.load_products()
            self.is_initialized = True
        else:
            # Juste rafraîchir les données sans recharger toute l'UI
            self.refresh_data()
    
    def refresh_data(self):
        """Rafraîchit les données sans reconstruire l'UI"""
        print("🔄 Rafraîchissement des données produits")
        app = App.get_running_app()
        db = app.db
        self.all_products = db.get_produits()
        self.apply_filters()
    
    def load_categories(self):
        """Charge les catégories pour le filtre"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT nom FROM categories ORDER BY nom")
            categories = cursor.fetchall()
            self.categories_list = [c[0] for c in categories]
            self.cat_spinner.values = ['Toutes'] + self.categories_list
        except Exception as e:
            print(f"❌ Erreur chargement catégories: {e}")
        finally:
            conn.close()
    
    def load_products(self):
        """Charge tous les produits"""
        app = App.get_running_app()
        db = app.db
        self.all_products = db.get_produits()
        self.apply_filters()
           
        
    def open_add_product(self, instance):
        """Ouvre le formulaire d'ajout de produit"""
        print("="*50)
        print("🔥🔥🔥 open_add_product est APPELÉE !!! 🔥🔥🔥")
        print("="*50)
        self.manager.current = 'product_form'
        self.manager.get_screen('product_form').set_mode('add')

    
    def apply_filters(self, *args):
        """Applique tous les filtres"""
        search = self.search_input.text.lower()
        category = self.cat_spinner.text if self.cat_spinner.text != 'Toutes' else None
        status_filter = self.status_spinner.text
        
        # Prix
        try:
            prix_min = float(self.prix_min_input.text) if self.prix_min_input.text else None
        except:
            prix_min = None
        try:
            prix_max = float(self.prix_max_input.text) if self.prix_max_input.text else None
        except:
            prix_max = None
        
        # Stock
        try:
            stock_min = int(self.stock_min_input.text) if self.stock_min_input.text else None
        except:
            stock_min = None
        try:
            stock_max = int(self.stock_max_input.text) if self.stock_max_input.text else None
        except:
            stock_max = None
        
        filtered = []
        for p in self.all_products:
            # p: id, nom, prix, quantite_stock, seuil_alerte, tva, description, barcode, categorie
            nom = p[1]
            prix = p[2]
            stock = p[3]
            seuil = p[4]
            categorie = p[8] if len(p) > 8 else 'Non catégorisé'
            actif = p[9] if len(p) > 9 else 1
            
            # Filtre recherche
            if search and search not in nom.lower():
                continue
            
            # Filtre catégorie
            if category and category != categorie:
                continue
            
            # Filtre statut
            if status_filter == 'Actif' and actif != 1:
                continue
            if status_filter == 'Inactif' and actif != 0:
                continue
            if status_filter == 'Rupture' and stock > 0:
                continue
            if status_filter == 'Alerte' and (stock > seuil or stock <= 0):
                continue
            
            # Filtre prix
            if prix_min is not None and prix < prix_min:
                continue
            if prix_max is not None and prix > prix_max:
                continue
            
            # Filtre stock
            if stock_min is not None and stock < stock_min:
                continue
            if stock_max is not None and stock > stock_max:
                continue
            
            filtered.append(p)
        
        self.display_products(filtered)
    
    def reset_filters(self, instance):
        """Réinitialise tous les filtres"""
        self.search_input.text = ''
        self.cat_spinner.text = 'Toutes'
        self.status_spinner.text = 'Tous'
        self.prix_min_input.text = ''
        self.prix_max_input.text = ''
        self.stock_min_input.text = ''
        self.stock_max_input.text = ''
        self.apply_filters()
    
    def on_search(self, instance, value):
        """Filtre lors de la recherche"""
        self.apply_filters()
    
    def display_products(self, products):
        """Affiche les produits avec menu contextuel"""
        self.list_layout.clear_widgets()
        
        if not products:
            self.list_layout.add_widget(Label(
                text='📭 Aucun produit trouvé\n\nAjoutez vos premiers produits !',
                font_size=14,
                color=(0.5, 0.5, 0.5, 1),
                size_hint_y=None,
                height=150,
                halign='center'
            ))
            return
        
        for p in products:
            # p: id, nom, prix, quantite_stock, seuil_alerte, tva, description, barcode, categorie, actif
            stock = p[3] if len(p) > 3 else 0
            seuil = p[4] if len(p) > 4 else 5
            prix = p[2] if len(p) > 2 else 0
            tva = p[5] if len(p) > 5 else 0
            categorie = p[8] if len(p) > 8 and p[8] else 'Non catégorisé'
            actif = p[9] if len(p) > 9 else 1
            
            # Statut du produit
            if actif == 0:
                status_text = "INACTIF"
                status_color = (0.5, 0.5, 0.5, 1)
                bg_color = (0.9, 0.9, 0.9, 0.3)
            elif stock <= 0:
                status_text = "RUPTURE"
                status_color = (1, 0, 0, 1)
                bg_color = (1, 0.8, 0.8, 0.3)
            elif stock <= seuil:
                status_text = f"ALERTE ({stock}/{seuil})"
                status_color = (1, 0.5, 0, 1)
                bg_color = (1, 0.9, 0.7, 0.3)
            else:
                status_text = f"Stock: {stock}"
                status_color = (0, 0.6, 0, 1)
                bg_color = (0.7, 0.9, 0.7, 0.2)
            
            # Carte
            card = BoxLayout(orientation='vertical', size_hint_y=None, height=140, padding=12, spacing=5)
            with card.canvas.before:
                Color(*bg_color)
                card.rect = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(12)])
            card.bind(pos=self._update_rect, size=self._update_rect)
            
            # Nom et catégorie
            line1 = BoxLayout(size_hint_y=None, height=35)
            nom_color = (0.5, 0.5, 0.5, 1) if actif == 0 else (1, 1, 1, 1)
            line1.add_widget(Label(text=f"{p[1][:35]}", font_size=15, bold=True, color=nom_color, halign='left'))
            
            cat_icon = self.get_category_icon(categorie)
            line1.add_widget(Label(text=f"{cat_icon} {categorie}", font_size=11, color=(0.6, 0.4, 0.2, 1), halign='right'))
            card.add_widget(line1)
            
            # Prix et TVA
            line2 = BoxLayout(size_hint_y=None, height=30)
            prix_text = f"{prix:,.0f} Fbu"
            if tva > 0:
                prix_text += f" (TVA {tva}%)"
            line2.add_widget(Label(text=prix_text, font_size=14, bold=True, color=(0.2, 0.6, 0.9, 1)))
            card.add_widget(line2)
            
            # Stock
            line3 = BoxLayout(size_hint_y=None, height=30)
            line3.add_widget(Label(text=status_text, font_size=12, bold=True, color=status_color))
            card.add_widget(line3)
            
            # Boutons d'action
            action_bar = BoxLayout(size_hint_y=None, height=35, spacing=5)
            
            edit_btn = Button(text="MODIFIER", size_hint_x=0.33, font_size=11, background_color=(0.2, 0.6, 0.9, 1))
            edit_btn.bind(on_press=lambda x, pid=p[0]: self.edit_product(pid))
            action_bar.add_widget(edit_btn)
            
            stock_btn = Button(text="STOCK", size_hint_x=0.33, font_size=11, background_color=(0.8, 0.5, 0.2, 1))
            stock_btn.bind(on_press=lambda x, pid=p[0], name=p[1], stock=stock: self.adjust_stock(pid, name, stock))
            action_bar.add_widget(stock_btn)
            
            delete_btn = Button(text="SUPPR", size_hint_x=0.33, font_size=11, background_color=(0.8, 0.2, 0.2, 1))
            delete_btn.bind(on_press=lambda x, pid=p[0], name=p[1]: self.delete_product(pid, name))
            action_bar.add_widget(delete_btn)
            
            card.add_widget(action_bar)
            
            self.list_layout.add_widget(card)
    
    def get_category_icon(self, categorie):
        """Retourne un texte pour la catégorie"""
        # Version sans emojis
        texts = {
            'Électronique': 'Electro',
            'Vêtements': 'Vetements',
            'Alimentation': 'Alim',
            'Maison': 'Maison',
            'Bureau': 'Bureau',
            'BOISSON': 'Boisson',
            'TABAC': 'Tabac',
            'INFORMATIQUE': 'Info'
        }
        return texts.get(categorie, categorie[:10] if categorie else 'Non cat')
    
    def _update_rect(self, instance, value):
        if hasattr(instance, 'rect'):
            instance.rect.pos = instance.pos
            instance.rect.size = instance.size
    
    def edit_product(self, product_id):
        """Ouvre le formulaire de modification de produit"""
        self.manager.current = 'product_form'
        self.manager.get_screen('product_form').set_mode('edit', product_id)
    
    def adjust_stock(self, product_id, product_name, current_stock):
        """Ajuste le stock d'un produit - Ajout ou retrait"""
        from kivy.uix.togglebutton import ToggleButton
        
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        content.add_widget(Label(text=f"Produit: {product_name}", font_size=14, bold=True))
        content.add_widget(Label(text=f"Stock actuel: {current_stock}", font_size=12))
        
        # Mode de fonctionnement (Ajouter ou Retirer)
        mode_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        mode_layout.add_widget(Label(text="Mode:", size_hint_x=0.3, font_size=12))
        
        self.btn_ajouter = ToggleButton(text="AJOUTER", group="stock_mode", state="down", size_hint_x=0.35)
        self.btn_ajouter.bind(on_press=self.on_mode_change)
        mode_layout.add_widget(self.btn_ajouter)
        
        self.btn_retirer = ToggleButton(text="RETIRER", group="stock_mode", size_hint_x=0.35)
        self.btn_retirer.bind(on_press=self.on_mode_change)
        mode_layout.add_widget(self.btn_retirer)
        
        content.add_widget(mode_layout)
        
        # Quantité à ajouter/retirer
        content.add_widget(Label(text="Quantité:", font_size=12, size_hint_y=None, height=30))
        stock_input = TextInput(
            hint_text="Quantité",
            multiline=False,
            input_filter='int',
            size_hint_y=None,
            height=50
        )
        content.add_widget(stock_input)
        
        # Type d'opération
        operation_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        operation_layout.add_widget(Label(text="Type:", size_hint_x=0.3, font_size=12))
        self.type_spinner = Spinner(
            text='Entrée (achat, réappro)',
            values=('Entrée (achat, réappro)', 'Sortie (vente, perte)', 'Ajustement'),
            size_hint_x=0.7,
            height=35
        )
        operation_layout.add_widget(self.type_spinner)
        content.add_widget(operation_layout)
        
        reason_input = TextInput(
            hint_text="Raison (ex: Réapprovisionnement, Vente client, Perte, etc.)",
            multiline=True,
            size_hint_y=None,
            height=80
        )
        content.add_widget(reason_input)
        
        # Aperçu du nouveau stock
        self.preview_label = Label(
            text=f"Nouveau stock: {current_stock}",
            font_size=12,
            color=(0.2, 0.8, 0.2, 1),
            size_hint_y=None,
            height=30
        )
        content.add_widget(self.preview_label)
        
        # Fonction pour mettre à jour l'aperçu
        def update_preview(*args):
            try:
                quantite = int(stock_input.text) if stock_input.text else 0
                mode_ajouter = self.btn_ajouter.state == 'down'
                
                if mode_ajouter:
                    nouveau = current_stock + quantite
                    self.preview_label.text = f"Nouveau stock: {nouveau} (+{quantite})"
                    self.preview_label.color = (0.2, 0.8, 0.2, 1)
                else:
                    nouveau = current_stock - quantite
                    if nouveau < 0:
                        self.preview_label.text = f"⚠️ Stock négatif! ({nouveau}) - Quantité trop élevée"
                        self.preview_label.color = (1, 0.5, 0, 1)
                    else:
                        self.preview_label.text = f"Nouveau stock: {nouveau} (-{quantite})"
                        self.preview_label.color = (1, 0.5, 0, 1) if quantite > 0 else (0.2, 0.8, 0.2, 1)
            except:
                self.preview_label.text = f"Nouveau stock: {current_stock}"
        
        stock_input.bind(text=update_preview)
        self.btn_ajouter.bind(on_press=update_preview)
        self.btn_retirer.bind(on_press=update_preview)
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        # ⚠️ CRÉER LA POPUP ICI (avant la fonction save_stock)
        popup = Popup(title="AJUSTER LE STOCK", content=content, size_hint=(0.9, 0.7))
        
        def save_stock(instance):
            try:
                quantite = int(stock_input.text) if stock_input.text else 0
                if quantite <= 0:
                    self.show_message("Erreur", "La quantité doit être supérieure à 0")
                    return
                
                mode_ajouter = self.btn_ajouter.state == 'down'
                type_operation = self.type_spinner.text
                reason = reason_input.text
                
                if mode_ajouter:
                    nouveau_stock = current_stock + quantite
                    type_mouvement = 'entree'
                    action_label = f"+{quantite}"
                    quantite_vendue = 0
                    quantite_ajoutee = quantite
                else:
                    if quantite > current_stock:
                        self.show_message("Erreur", f"Stock insuffisant! Actuel: {current_stock}, Retrait demandé: {quantite}")
                        return
                    nouveau_stock = current_stock - quantite
                    type_mouvement = 'sortie'
                    action_label = f"-{quantite}"
                    quantite_vendue = quantite
                    quantite_ajoutee = 0
                
                popup.dismiss()  # ⭐ popup est maintenant définie
                
                app = App.get_running_app()
                conn = app.db.get_connection()
                cursor = conn.cursor()
                
                cursor.execute("UPDATE produits SET quantite_stock = ? WHERE id = ?", (nouveau_stock, product_id))
                
                if 'Entrée' in type_operation:
                    type_historique = 'entree'
                    raison_detail = f"{type_operation} - {reason}" if reason else type_operation
                elif 'Sortie' in type_operation:
                    type_historique = 'sortie'
                    raison_detail = f"{type_operation} - {reason}" if reason else type_operation
                else:
                    type_historique = 'ajustement'
                    raison_detail = reason if reason else f"Ajustement {action_label}"
                
                cursor.execute("""
                    INSERT INTO mouvements_stock 
                    (produit_id, type, quantite, date, notes, utilisateur, ancien_stock, nouveau_stock)
                    VALUES (?, ?, ?, datetime('now', 'localtime'), ?, ?, ?, ?)
                """, (product_id, type_historique, quantite, raison_detail, 'Mobile', current_stock, nouveau_stock))
                
                conn.commit()
                
                # ⭐⭐⭐ LOG PRODUIT ⭐⭐⭐
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'produit_stock',
                        'Produits',
                        f"Ajustement stock {product_name}: {current_stock} → {nouveau_stock} ({action_label}) - {raison_detail}"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log: {e}")
                
                if mode_ajouter:
                    message = f"✅ {quantite} ajouté(s) au stock\nStock: {current_stock} → {nouveau_stock}"
                else:
                    message = f"✅ {quantite} retiré(s) du stock\nStock: {current_stock} → {nouveau_stock}"
                
                self.show_message("Succès", message)
                
                if app.network and app.network.connected:
                    stock_data = {
                        'id': product_id,
                        'nom': product_name,
                        'ancien_stock': current_stock,
                        'nouveau_stock': nouveau_stock,
                        'quantite_vendue': quantite_vendue,
                        'quantite_ajoutee': quantite_ajoutee,
                        'type': type_mouvement,
                        'raison': raison_detail
                    }
                    app.network.send_update('produits', 'update_stock', stock_data)
                    
                    if mode_ajouter:
                        print(f"📤 Envoi ajout stock: {product_name} +{quantite} → {nouveau_stock}")
                    else:
                        print(f"📤 Envoi retrait stock: {product_name} -{quantite} → {nouveau_stock}")
                
                self.load_products()
                
            except ValueError:
                self.show_message("Erreur", "Veuillez entrer un nombre valide")
            except Exception as e:
                print(f"❌ Erreur ajustement stock: {e}")
                self.show_message("Erreur", str(e)[:50])
            finally:
                if 'conn' in locals():
                    conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        save_btn = Button(text="VALIDER", background_color=(0.2, 0.7, 0.3, 1))
        save_btn.bind(on_press=save_stock)
        buttons.add_widget(save_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup.open()  # ⭐ Ouvrir la popup à la fin

    def on_mode_change(self, instance):
        """Change le mode d'ajustement"""
        # Mettre à jour l'aperçu quand le mode change
        if hasattr(self, 'preview_label') and hasattr(self, 'btn_ajouter') and hasattr(self, 'btn_retirer'):
            # Récupérer la valeur actuelle du stock
            current_stock = int(self.preview_label.text.split(":")[1].split()[0]) if ":" in self.preview_label.text else 0
            # Mettre à jour l'aperçu
            mode_ajouter = self.btn_ajouter.state == 'down'
            try:
                quantite = int(self.stock_input.text) if hasattr(self, 'stock_input') and self.stock_input.text else 0
                if mode_ajouter:
                    nouveau = current_stock + quantite
                    self.preview_label.text = f"Nouveau stock: {nouveau} (+{quantite})"
                else:
                    nouveau = current_stock - quantite
                    if nouveau < 0:
                        self.preview_label.text = f"⚠️ Stock négatif! ({nouveau}) - Quantité trop élevée"
                    else:
                        self.preview_label.text = f"Nouveau stock: {nouveau} (-{quantite})"
            except:
                pass

    
    def delete_product(self, product_id, product_name):
        """Supprime un produit après vérification des factures associées"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            # Vérifier si le produit est utilisé dans des factures
            cursor.execute("""
                SELECT COUNT(*) FROM lignes_facture l
                JOIN factures f ON l.facture_id = f.id
                WHERE l.produit_id = ?
            """, (product_id,))
            facture_count = cursor.fetchone()[0]
            
            # Récupérer quelques factures pour info
            factures_info = []
            if facture_count > 0:
                cursor.execute("""
                    SELECT DISTINCT f.numero, f.date
                    FROM lignes_facture l
                    JOIN factures f ON l.facture_id = f.id
                    WHERE l.produit_id = ?
                    LIMIT 3
                """, (product_id,))
                factures_info = cursor.fetchall()
            
            # Créer le contenu du message
            content = BoxLayout(orientation='vertical', padding=10, spacing=10)
            
            if facture_count > 0:
                # Produit utilisé dans des factures - ne pas autoriser la suppression
                message = f"❌ Impossible de supprimer {product_name}\n\n"
                message += f"Ce produit est utilisé dans {facture_count} facture(s) :\n\n"
                for f in factures_info:
                    message += f"• {f[0]} du {f[1][:10]}\n"
                message += f"\nPour des raisons d'intégrité des données, la suppression n'est pas autorisée."
                message += f"\n\n✓ Le produit peut être désactivé (option ci-dessous)"
                
                content.add_widget(Label(text=message, font_size=12, size_hint_y=None, height=200))
                
                buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
                
                # Bouton désactiver
                desactiver_btn = Button(text="🔘 DÉSACTIVER", background_color=(0.8, 0.5, 0.2, 1))
                desactiver_btn.bind(on_press=lambda x: self.desactivate_product(product_id, product_name, popup))
                buttons.add_widget(desactiver_btn)
                
                # Bouton annuler
                annuler_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
                annuler_btn.bind(on_press=lambda x: popup.dismiss())
                buttons.add_widget(annuler_btn)
                
                content.add_widget(buttons)
                
            else:
                # Produit non utilisé - autoriser la suppression
                message = f"Supprimer {product_name} ?\n\n"
                message += "Cette action est irréversible.\n"
                message += "Le produit sera définitivement supprimé de la base."
                
                content.add_widget(Label(text=message, font_size=14))
                
                buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
                
                def do_delete(instance):
                    popup.dismiss()
                    self.perform_delete(product_id, product_name)
                
                def cancel(instance):
                    popup.dismiss()
                
                delete_btn = Button(text="SUPPRIMER", background_color=(0.8, 0.2, 0.2, 1))
                delete_btn.bind(on_press=do_delete)
                buttons.add_widget(delete_btn)
                
                cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
                cancel_btn.bind(on_press=cancel)
                buttons.add_widget(cancel_btn)
                
                content.add_widget(buttons)
            
            popup = Popup(
                title="CONFIRMER SUPPRESSION" if facture_count == 0 else "❌ SUPPRESSION IMPOSSIBLE",
                content=content,
                size_hint=(0.9, 0.6) if facture_count > 0 else (0.8, 0.5)
            )
            popup.open()
            
        except Exception as e:
            print(f"❌ Erreur vérification produit: {e}")
            self.show_message("Erreur", str(e))
        finally:
            conn.close()

    def desactivate_product(self, product_id, product_name, parent_popup):
        """Désactive un produit (alternative à la suppression)"""
        parent_popup.dismiss()
        
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(
            text=f"Désactiver {product_name} ?\n\n"
                 f"Le produit ne sera plus visible dans le catalogue,\n"
                 f"mais restera dans l'historique des factures.\n\n"
                 f"Vous pourrez le réactiver plus tard si nécessaire.",
            font_size=14
        ))
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def do_desactivate(instance):
            popup.dismiss()
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            try:
                cursor.execute("UPDATE produits SET actif = 0 WHERE id = ?", (product_id,))
                conn.commit()
                
                # ⭐⭐⭐ AJOUTER LE LOG PRODUIT (DÉSACTIVATION) ⭐⭐⭐
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'produit_desactivation',
                        'Produits',
                        f"Désactivation produit: {product_name} (ID: {product_id})"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log: {e}")
                
                self.show_message("Succès", f"Produit {product_name} désactivé")
                self.load_products()
            except Exception as e:
                print(f"❌ Erreur désactivation: {e}")
                self.show_message("Erreur", str(e))
            finally:
                conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        desactiver_btn = Button(text="DÉSACTIVER", background_color=(0.8, 0.5, 0.2, 1))
        desactiver_btn.bind(on_press=do_desactivate)
        buttons.add_widget(desactiver_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="🔘 DÉSACTIVER LE PRODUIT", content=content, size_hint=(0.8, 0.5))
        popup.open()

    def perform_delete(self, product_id, product_name):
        """Exécute la suppression du produit"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("DELETE FROM produits WHERE id = ?", (product_id,))
            conn.commit()
            
            # ⭐⭐⭐ AJOUTER LE LOG PRODUIT (SUPPRESSION) ⭐⭐⭐
            try:
                app.db.add_log(
                    app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                    'produit_suppression',
                    'Produits',
                    f"Suppression produit: {product_name} (ID: {product_id})"
                )
            except Exception as e:
                print(f"⚠️ Erreur log: {e}")
            
            self.show_message("Succès", f"Produit {product_name} supprimé")
            self.load_products()
            
            if app.network and app.network.connected:
                delete_data = {
                    'id': product_id,
                    'nom': product_name,
                    'action': 'delete'
                }
                app.network.send_update('produits', 'delete', delete_data)
            
        except Exception as e:
            print(f"❌ Erreur suppression: {e}")
            self.show_message("Erreur", str(e))
        finally:
            conn.close()
    
    def show_message(self, title, message):
        """Affiche un message temporaire"""
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 2)
    
    def go_back(self, instance):
        self.manager.current = 'dashboard'
        
# ============================================================================
# ÉCRAN Formulaire d'ajout/modification de produit
# ============================================================================        
        
class ProductFormScreen(Screen):
    """Formulaire d'ajout/modification de produit"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mode = 'add'
        self.product_id = None
        self.categories = []
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
        
        # En-tête
        header = BoxLayout(size_hint_y=0.1)
        back_btn = Button(text='GO BACK', size_hint_x=0.15, background_color=(0.5, 0.5, 0.5, 1))
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        self.title_label = Label(text="", font_size=18, bold=True)
        header.add_widget(self.title_label)
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Champs du formulaire
        scroll = ScrollView()
        form = BoxLayout(orientation='vertical', size_hint_y=None, spacing=10)
        form.bind(minimum_height=form.setter('height'))
        
        # Nom
        self.nom_input = TextInput(hint_text="Nom du produit *", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.nom_input)
        
        # Code-barres
        self.barcode_input = TextInput(hint_text="Code-barres (optionnel)", multiline=False, size_hint_y=None, height=50)
        form.add_widget(self.barcode_input)
        
        # Catégorie
        cat_layout = BoxLayout(size_hint_y=None, height=50, spacing=5)
        cat_layout.add_widget(Label(text="Catégorie:", size_hint_x=0.3))
        self.cat_spinner = Spinner(text="Sélectionner", values=[], size_hint_x=0.7)
        cat_layout.add_widget(self.cat_spinner)
        form.add_widget(cat_layout)
        
        # Prix
        self.prix_input = TextInput(hint_text="Prix de vente (Fbu) *", multiline=False, size_hint_y=None, height=50, input_filter='float')
        form.add_widget(self.prix_input)
        
        # Prix d'achat
        self.prix_achat_input = TextInput(hint_text="Prix d'achat (Fbu)", multiline=False, size_hint_y=None, height=50, input_filter='float')
        form.add_widget(self.prix_achat_input)
        
        # TVA
        self.tva_input = TextInput(hint_text="TVA (%)", text="18", multiline=False, size_hint_y=None, height=50, input_filter='float')
        form.add_widget(self.tva_input)
        
        # Stock
        self.stock_input = TextInput(hint_text="Quantité en stock", text="0", multiline=False, size_hint_y=None, height=50, input_filter='int')
        form.add_widget(self.stock_input)
        
        # Seuil d'alerte
        self.seuil_input = TextInput(hint_text="Seuil d'alerte", text="5", multiline=False, size_hint_y=None, height=50, input_filter='int')
        form.add_widget(self.seuil_input)
        
        # Description
        self.description_input = TextInput(hint_text="Description", multiline=True, size_hint_y=None, height=100)
        form.add_widget(self.description_input)
        
        scroll.add_widget(form)
        layout.add_widget(scroll)
        
        # Boutons
        buttons = BoxLayout(size_hint_y=None, height=60, spacing=10)
        save_btn = Button(text="ENREGISTRER", background_color=(0.2, 0.7, 0.3, 1))
        save_btn.bind(on_press=self.save_product)
        buttons.add_widget(save_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.8, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=self.go_back)
        buttons.add_widget(cancel_btn)
        
        layout.add_widget(buttons)
        
        self.add_widget(layout)
    
    def on_enter(self):
        """Charge les catégories"""
        print("📱 ProductFormScreen.on_enter appelé")
        self.load_categories()
    
    def load_categories(self):
        """Charge les catégories depuis la base"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT id, nom FROM categories ORDER BY nom")
            self.categories = cursor.fetchall()
            self.cat_spinner.values = [c[1] for c in self.categories]
            if self.categories:
                self.cat_spinner.text = self.categories[0][1]
        except Exception as e:
            print(f"❌ Erreur chargement catégories: {e}")
        finally:
            conn.close()
    
    def set_mode(self, mode, product_id=None):
        """Définit le mode (add/edit)"""
        self.mode = mode
        self.product_id = product_id
        
        if mode == 'add':
            self.title_label.text = "➕ NOUVEAU PRODUIT"
            self.clear_form()
        else:
            self.title_label.text = "MODIFIER PRODUIT"
            self.load_product(product_id)
    
    def clear_form(self):
        """Efface le formulaire"""
        self.nom_input.text = ""
        self.barcode_input.text = ""
        self.prix_input.text = ""
        self.prix_achat_input.text = ""
        self.tva_input.text = "18"
        self.stock_input.text = "0"
        self.seuil_input.text = "5"
        self.description_input.text = ""
    
    def load_product(self, product_id):
        """Charge les données du produit"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                SELECT nom, barcode, categorie_id, prix, prix_achat, tva, quantite_stock, seuil_alerte, description
                FROM produits WHERE id = ?
            """, (product_id,))
            product = cursor.fetchone()
            
            if product:
                self.nom_input.text = product[0] or ""
                self.barcode_input.text = product[1] or ""
                
                # Sélectionner la catégorie
                cat_id = product[2]
                for cat in self.categories:
                    if cat[0] == cat_id:
                        self.cat_spinner.text = cat[1]
                        break
                
                self.prix_input.text = str(product[3]) if product[3] else ""
                self.prix_achat_input.text = str(product[4]) if product[4] else ""
                self.tva_input.text = str(product[5]) if product[5] else "18"
                self.stock_input.text = str(product[6]) if product[6] is not None else "0"
                self.seuil_input.text = str(product[7]) if product[7] else "5"
                self.description_input.text = product[8] or ""
        except Exception as e:
            print(f"❌ Erreur load_product: {e}")
        finally:
            conn.close()
           
    
    def save_product(self, instance):
        """Enregistre le produit"""
        nom = self.nom_input.text.strip()
        if not nom:
            self.show_message("Erreur", "Le nom du produit est obligatoire")
            return
        
        try:
            prix = float(self.prix_input.text) if self.prix_input.text else 0
            prix_achat = float(self.prix_achat_input.text) if self.prix_achat_input.text else 0
            tva = float(self.tva_input.text) if self.tva_input.text else 18
            stock = int(self.stock_input.text) if self.stock_input.text else 0
            seuil = int(self.seuil_input.text) if self.seuil_input.text else 5
        except ValueError:
            self.show_message("Erreur", "Valeurs numériques invalides")
            return
        
        # Trouver l'ID de la catégorie
        categorie_id = None
        categorie_nom = None
        for cat in self.categories:
            if cat[1] == self.cat_spinner.text:
                categorie_id = cat[0]
                categorie_nom = cat[1]
                break
        
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            if self.mode == 'add':
                product_uuid = str(uuid.uuid4())
                cursor.execute('''
                    INSERT INTO produits 
                    (nom, barcode, categorie_id, categorie, prix, prix_achat, tva, 
                     quantite_stock, seuil_alerte, description, created_at, uuid, actif)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (nom, self.barcode_input.text, categorie_id, categorie_nom, prix, prix_achat, tva,
                      stock, seuil, self.description_input.text, datetime.now().isoformat(), product_uuid, 1))
                
                message = "Produit ajouté avec succès"
                
                # ⭐⭐⭐ LOG AJOUT PRODUIT ⭐⭐⭐
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'produit_ajout',
                        'Produits',
                        f"Ajout produit: {nom} - Prix: {prix:,.0f} Fbu - Stock: {stock} - TVA: {tva}%"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log ajout produit: {e}")
                
                # Synchronisation
                if app.network and app.network.connected:
                    product_data = {
                        'nom': nom,
                        'barcode': self.barcode_input.text,
                        'categorie_id': categorie_id,
                        'categorie': categorie_nom,
                        'prix': prix,
                        'prix_achat': prix_achat,
                        'tva': tva,
                        'quantite_stock': stock,
                        'seuil_alerte': seuil,
                        'description': self.description_input.text,
                        'created_at': datetime.now().isoformat(),
                        'uuid': product_uuid
                    }
                    app.network.send_update('produits', 'insert', product_data)
                    print(f"📤 Produit {nom} envoyé au serveur")
                else:
                    print(f"⚠️ Non connecté au serveur - Produit enregistré localement seulement")
                
            else:  # mode edit
                cursor.execute('''
                    UPDATE produits SET 
                        nom = ?, barcode = ?, categorie_id = ?, categorie = ?, prix = ?, prix_achat = ?,
                        tva = ?, quantite_stock = ?, seuil_alerte = ?, description = ?
                    WHERE id = ?
                ''', (nom, self.barcode_input.text, categorie_id, categorie_nom, prix, prix_achat,
                      tva, stock, seuil, self.description_input.text, self.product_id))
                
                message = "Produit modifié avec succès"
                
                # ⭐⭐⭐ LOG MODIFICATION PRODUIT ⭐⭐⭐
                try:
                    app.db.add_log(
                        app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                        'produit_modification',
                        'Produits',
                        f"Modification produit: {nom} (ID: {self.product_id}) - Prix: {prix:,.0f} Fbu - Stock: {stock}"
                    )
                except Exception as e:
                    print(f"⚠️ Erreur log modification produit: {e}")
                
                # Synchronisation pour la modification
                if app.network and app.network.connected:
                    product_data = {
                        'id': self.product_id,
                        'nom': nom,
                        'barcode': self.barcode_input.text,
                        'categorie_id': categorie_id,
                        'categorie': categorie_nom,
                        'prix': prix,
                        'prix_achat': prix_achat,
                        'tva': tva,
                        'quantite_stock': stock,
                        'seuil_alerte': seuil,
                        'description': self.description_input.text
                    }
                    app.network.send_update('produits', 'update', product_data)
                    print(f"📤 Modification produit {nom} envoyée au serveur")
            
            conn.commit()
            self.show_message("Succès", message)
            
            # Rafraîchir la liste des produits
            produits_screen = self.manager.get_screen('produits')
            if hasattr(produits_screen, 'refresh_data'):
                produits_screen.refresh_data()
            else:
                produits_screen.load_products()
            
            self.go_back(None)
            
        except Exception as e:
            print(f"❌ Erreur save_product: {e}")
            import traceback
            traceback.print_exc()
            self.show_message("Erreur", str(e)[:50])
            conn.rollback()
        finally:
            conn.close()
    
    def show_message(self, title, message):
        """Affiche un message temporaire"""
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 2)
    
    def go_back(self, instance):
        self.manager.current = 'produits'        
        
        
        
        
    

# ============================================================================
# ÉCRAN VENTES
# ============================================================================

class VentesScreen(Screen):
    """Écran des ventes avec filtres avancés"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.app = App.get_running_app()
        self.all_ventes = []
        self.filters_visible = False
        self.filters_panel_added = False
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='HISTORIQUE DES VENTES', font_size=18, bold=True))
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Barre de recherche
        search_layout = BoxLayout(size_hint=(1, 0.06), padding=5, spacing=5)
        search_layout.add_widget(Label(text='', size_hint_x=0.1, font_size=16))
        self.search_input = TextInput(
            hint_text='Rechercher par numéro ou client...',
            multiline=False,
            size_hint_x=0.9,
            font_size=13,
            height=35,
            background_color=(0.95, 0.95, 0.95, 1)
        )
        self.search_input.bind(text=self.apply_filters)
        search_layout.add_widget(self.search_input)
        layout.add_widget(search_layout)
        
        # Bouton FILTRES
        self.filters_visible = False
        self.filter_btn = Button(
            text='FILTRES AVANCES', 
            size_hint_y=None, 
            height=40,
            background_color=(0.3, 0.3, 0.4, 1)
        )
        self.filter_btn.bind(on_press=self.toggle_filters)
        layout.add_widget(self.filter_btn)
        
        # Créer le panneau des filtres
        self.create_filters_panel()
        self.filters_panel_added = False
        
        # Liste des ventes
        self.scroll = ScrollView()
        self.list_layout = BoxLayout(orientation='vertical', spacing=8, padding=10, size_hint_y=None)
        self.list_layout.bind(minimum_height=self.list_layout.setter('height'))
        self.scroll.add_widget(self.list_layout)
        layout.add_widget(self.scroll)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('NOUVEAU', 'nouvelle_vente'),
            ('PRODUITS', 'produits')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12, bold=True)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        
        self.add_widget(layout)
    
    def create_filters_panel(self):
        """Crée le panneau des filtres"""
        self.filters_panel = BoxLayout(orientation='vertical', size_hint_y=None, spacing=5, padding=5)
        self.filters_panel.height = 280
        self.filters_panel.opacity = 1
        self.filters_panel.disabled = False
        
        # Filtre période
        period_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        period_layout.add_widget(Label(text="Période:", size_hint_x=0.3, font_size=12))
        self.period_spinner = Spinner(
            text='Toutes',
            values=['Toutes', "Aujourd'hui", 'Cette semaine', 'Ce mois', 'Cette année'],
            size_hint_x=0.7,
            height=35
        )
        self.period_spinner.bind(text=self.apply_filters)
        period_layout.add_widget(self.period_spinner)
        self.filters_panel.add_widget(period_layout)
        
        # Filtre client
        client_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        client_layout.add_widget(Label(text="Client:", size_hint_x=0.3, font_size=12))
        self.client_input = TextInput(
            hint_text="Nom du client",
            multiline=False,
            size_hint_x=0.7,
            height=35
        )
        self.client_input.bind(text=self.apply_filters)
        client_layout.add_widget(self.client_input)
        self.filters_panel.add_widget(client_layout)
        
        # Filtre montant min/max
        montant_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        montant_layout.add_widget(Label(text="Montant min:", size_hint_x=0.3, font_size=12))
        self.montant_min_input = TextInput(
            text='', 
            multiline=False, 
            size_hint_x=0.3, 
            height=35, 
            input_filter='float'
        )
        self.montant_min_input.bind(text=self.apply_filters)
        montant_layout.add_widget(self.montant_min_input)
        
        montant_layout.add_widget(Label(text="Montant max:", size_hint_x=0.2, font_size=12))
        self.montant_max_input = TextInput(
            text='', 
            multiline=False, 
            size_hint_x=0.3, 
            height=35, 
            input_filter='float'
        )
        self.montant_max_input.bind(text=self.apply_filters)
        montant_layout.add_widget(self.montant_max_input)
        self.filters_panel.add_widget(montant_layout)
        
        # Filtre statut paiement
        statut_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        statut_layout.add_widget(Label(text="Statut paiement:", size_hint_x=0.3, font_size=12))
        self.statut_spinner = Spinner(
            text='Tous',
            values=['Tous', 'payée', 'impayée', 'annulée', 'en attente', 'partiellement payée'],
            size_hint_x=0.7,
            height=35
        )
        self.statut_spinner.bind(text=self.apply_filters)
        statut_layout.add_widget(self.statut_spinner)
        self.filters_panel.add_widget(statut_layout)
        
        # Filtre synchronisation
        sync_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        sync_layout.add_widget(Label(text="Synchronisation:", size_hint_x=0.3, font_size=12))
        self.sync_spinner = Spinner(
            text='Tous',
            values=['Tous', 'synced', 'pending'],
            size_hint_x=0.7,
            height=35
        )
        self.sync_spinner.bind(text=self.apply_filters)
        sync_layout.add_widget(self.sync_spinner)
        self.filters_panel.add_widget(sync_layout)
        
        # Boutons
        btn_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        reset_btn = Button(text="Réinitialiser", size_hint_x=0.5, background_color=(0.8, 0.3, 0.3, 1))
        reset_btn.bind(on_press=self.reset_filters)
        btn_layout.add_widget(reset_btn)
        
        apply_btn = Button(text="Appliquer", size_hint_x=0.5, background_color=(0.2, 0.7, 0.3, 1))
        apply_btn.bind(on_press=self.apply_filters)
        btn_layout.add_widget(apply_btn)
        self.filters_panel.add_widget(btn_layout)
    
    def toggle_filters(self, instance):
        """Affiche/masque les filtres avancés"""
        print("🔍 toggle_filters appelé")
        
        # Récupérer le layout principal
        layout = self.children[0]
        
        if self.filters_visible:
            # Supprimer le panel
            if self.filters_panel_added:
                layout.remove_widget(self.filters_panel)
                self.filters_panel_added = False
            self.filter_btn.text = 'FILTRES AVANCES'
            self.filters_visible = False
            print("✅ Filtres supprimés")
        else:
            # Ajouter le panel après le bouton FILTRES
            filter_btn_index = None
            for i, child in enumerate(layout.children):
                if child == self.filter_btn:
                    filter_btn_index = i
                    break
            
            if filter_btn_index is not None:
                layout.add_widget(self.filters_panel, index=filter_btn_index)
            else:
                layout.add_widget(self.filters_panel)
            self.filters_panel_added = True
            
            self.filter_btn.text = 'MASQUER FILTRES'
            self.filters_visible = True
            print("✅ Filtres ajoutés")
    
    def on_enter(self):
        self.load_ventes()
    
    def load_ventes(self):
        """Charge toutes les ventes"""
        self.all_ventes = self.get_ventes_avec_clients()
        self.apply_filters()
    
    def get_ventes_avec_clients(self):
        """Récupère les ventes avec toutes les infos clients"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT 
                    f.id,                    -- index 0
                    f.numero,               -- index 1
                    f.date,                 -- index 2
                    COALESCE(c.nom, 'Client inconnu') as client_nom,  -- index 3
                    f.total_ht,             -- index 4 ⭐ AJOUTER
                    f.total_tva,            -- index 5 ⭐ AJOUTER
                    f.total_ttc,            -- index 6
                    f.statut,               -- index 7
                    f.mode_paiement,        -- index 8
                    c.id as client_id,      -- index 9
                    c.telephone,            -- index 10
                    c.email,                -- index 11
                    c.adresse,              -- index 12
                    c.ville,                -- index 13
                    f.sync_status           -- index 14
                FROM factures f
                LEFT JOIN clients c ON f.client_id = c.id
                ORDER BY f.id DESC
            """)
            results = cursor.fetchall()
            
            # Debug
            print(f"\n📊 {len(results)} factures chargées:")
            for r in results[:3]:
                print(f"   ID:{r[0]}, N°:{r[1]}, Client:{r[3]}")
                print(f"      HT:{r[4]}, TVA:{r[5]}, TTC:{r[6]}")
                print(f"      Statut:{r[7]}, Paiement:{r[8]}")
            
            return results
        except Exception as e:
            print(f"❌ Erreur get_ventes_avec_clients: {e}")
            import traceback
            traceback.print_exc()
            return []
        finally:
            conn.close()
    
    def apply_filters(self, *args):
        """Applique tous les filtres"""
        filtered = self.all_ventes.copy()
        
        # Filtre recherche (numéro ou client)
        search = self.search_input.text.lower()
        if search:
            # v[1] = numero, v[3] = client_nom
            filtered = [v for v in filtered if search in str(v[1]).lower() or search in str(v[3]).lower()]
        
        # Filtre période (v[2] = date)
        periode = self.period_spinner.text
        if periode != 'Toutes':
            filtered = self.filter_by_period(filtered, periode)
        
        # Filtre client (v[3] = client_nom)
        client = self.client_input.text.strip().lower()
        if client:
            filtered = [v for v in filtered if client in str(v[3]).lower()]
        
        # Filtre montant (v[6] = total_ttc)
        try:
            montant_min = float(self.montant_min_input.text) if self.montant_min_input.text else None
            montant_max = float(self.montant_max_input.text) if self.montant_max_input.text else None
            
            if montant_min is not None:
                filtered = [v for v in filtered if v[6] >= montant_min]
            if montant_max is not None:
                filtered = [v for v in filtered if v[6] <= montant_max]
        except:
            pass
        
        # Filtre statut paiement (v[7] = statut)
        statut = self.statut_spinner.text
        if statut != 'Tous':
            filtered = [v for v in filtered if v[7] == statut]
        
        # Filtre synchronisation (v[14] = sync_status)
        sync = self.sync_spinner.text
        if sync != 'Tous':
            filtered = [v for v in filtered if v[14] == sync]
        
        self.display_ventes(filtered)
    
    def filter_by_period(self, ventes, periode):
        """Filtre les ventes par période"""
        from datetime import datetime, timedelta
        
        today = datetime.now().date()
        
        if periode == "Aujourd'hui":
            date_str = today.strftime('%Y-%m-%d')
            # v[2] = date de la facture
            return [v for v in ventes if v[2].startswith(date_str)]
        
        elif periode == "Cette semaine":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
            return [v for v in ventes if self.date_in_range(v[2], start, end)]
        
        elif periode == "Ce mois":
            month_str = today.strftime('%Y-%m')
            return [v for v in ventes if v[2].startswith(month_str)]
        
        elif periode == "Cette année":
            year_str = today.strftime('%Y')
            return [v for v in ventes if v[2].startswith(year_str)]
        
        return ventes
    
    def date_in_range(self, date_str, start, end):
        """Vérifie si une date est dans un intervalle"""
        try:
            date = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
            return start <= date <= end
        except:
            return False
    
    def reset_filters(self, instance):
        """Réinitialise tous les filtres"""
        self.search_input.text = ''
        self.period_spinner.text = 'Toutes'
        self.client_input.text = ''
        self.montant_min_input.text = ''
        self.montant_max_input.text = ''
        self.statut_spinner.text = 'Tous'
        self.sync_spinner.text = 'Tous'
        self.apply_filters()
    
    def display_ventes(self, ventes):
        """Affiche les ventes filtrées"""
        self.list_layout.clear_widgets()
        
        if not ventes:
            self.list_layout.add_widget(Label(
                text='📭 Aucune vente trouvée\n\nModifiez vos filtres ou créez une vente !',
                font_size=14,
                color=(0.5, 0.5, 0.5, 1),
                size_hint_y=None,
                height=150,
                halign='center'
            ))
            return
        
        for v in ventes:
            # ⭐⭐⭐ INDEX CORRECTS ⭐⭐⭐
            facture_id = v[0]           # ID
            numero = v[1]               # N° Facture
            date_texte = v[2][:10] if v[2] else ''
            client = v[3]               # Client
            total_ht = v[4] if v[4] else 0      # ⭐ Total HT
            total_tva = v[5] if v[5] else 0     # ⭐ Total TVA
            total_ttc = v[6] if v[6] else 0     # ⭐ Total TTC
            statut = v[7] if v[7] else 'payée'  # ⭐ Statut
            mode_paiement = v[8] if v[8] else 'Espèces'  # ⭐ Paiement
            client_id = v[9] if len(v) > 9 else None
            client_tel = v[10] if len(v) > 10 else ''
            client_email = v[11] if len(v) > 11 else ''
            client_adresse = v[12] if len(v) > 12 else ''
            client_ville = v[13] if len(v) > 13 else ''
            sync_status = v[14] if len(v) > 14 else 'synced'
            
            # Debug (afficher les 5 premières)
            if len(self.list_layout.children) < 5:
                print(f"Facture {numero}: HT={total_ht}, TVA={total_tva}, TTC={total_ttc}, Statut={statut}, Paiement={mode_paiement}")
            
            # Couleurs selon statut de synchronisation
            if sync_status == 'pending':
                bg_color = (0.9, 0.7, 0.3, 0.3)
                sync_color = (0.9, 0.5, 0, 1)
                sync_text = 'EN ATTENTE'
            else:
                bg_color = (0.3, 0.7, 0.4, 0.2)
                sync_color = (0, 0.6, 0, 1)
                sync_text = 'SYNCHRONISÉ'
            
            # Couleurs selon statut de paiement
            if statut == 'impayée':
                statut_color = (0.8, 0.2, 0.2, 1)
                statut_text = 'IMPAYÉE'
            elif statut == 'partiellement payée':
                statut_color = (0.9, 0.6, 0.1, 1)
                statut_text = 'PARTIELLEMENT PAYÉE'
            elif statut == 'annulée':
                statut_color = (0.5, 0.5, 0.5, 1)
                statut_text = 'ANNULÉE'
            elif statut == 'en attente':
                statut_color = (0.9, 0.5, 0, 1)
                statut_text = 'EN ATTENTE'
            else:  # payée
                statut_color = (0, 0.6, 0, 1)
                statut_text = 'PAYÉE'
            
            # Carte
            card = BoxLayout(orientation='vertical', size_hint_y=None, height=200, padding=10, spacing=5)
            
            with card.canvas.before:
                Color(*bg_color)
                card.rect = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(10)])
            
            card.bind(pos=self._update_rect, size=self._update_rect)
            
            # Numéro et date
            line1 = BoxLayout(size_hint_y=None, height=30)
            line1.add_widget(Label(text=f"{numero}", font_size=14, bold=True, halign='left'))
            line1.add_widget(Label(text=f"{date_texte}", font_size=12, halign='right'))
            card.add_widget(line1)
            
            # Client
            client_layout = BoxLayout(size_hint_y=None, height=30)
            client_layout.add_widget(Label(text=f"{client}", font_size=13, halign='left'))
            card.add_widget(client_layout)
            
            # Montants (HT, TVA, TTC)
            line_montants = BoxLayout(size_hint_y=None, height=35)
            line_montants.add_widget(Label(
                text=f"HT: {total_ht:,.0f}",
                font_size=12,
                color=(0.2, 0.6, 0.9, 1),
                halign='left'
            ))
            line_montants.add_widget(Label(
                text=f"TVA: {total_tva:,.0f}",
                font_size=12,
                color=(1, 0.5, 0, 1),
                halign='center'
            ))
            line_montants.add_widget(Label(
                text=f"TTC: {total_ttc:,.0f}",
                font_size=14,
                bold=True,
                color=(0.2, 0.8, 0.2, 1),
                halign='right'
            ))
            card.add_widget(line_montants)
            
            # Statut et paiement
            line_statut = BoxLayout(size_hint_y=None, height=30)
            line_statut.add_widget(Label(
                text=statut_text,
                font_size=12,
                bold=True,
                color=statut_color,
                halign='left'
            ))
            line_statut.add_widget(Label(
                text=f"Paiement: {mode_paiement}",
                font_size=12,
                color=(0.2, 0.8, 0.2, 1),
                halign='right'
            ))
            card.add_widget(line_statut)
            
            # Statut synchronisation
            line4 = BoxLayout(size_hint_y=None, height=25)
            line4.add_widget(Label(text=sync_text, font_size=10, color=sync_color, halign='left'))
            card.add_widget(line4)
            
            # Bouton action
            action_bar = BoxLayout(size_hint_y=None, height=35, spacing=5)
            action_btn = Button(
                text="ACTIONS", 
                size_hint_x=1.0, 
                font_size=12, 
                bold=True,
                background_color=(0.2, 0.6, 0.9, 1)
            )
            action_btn.bind(on_press=lambda x, fid=facture_id, fnum=numero, fclient=client, 
                            ftel=client_tel, femail=client_email, faddr=client_adresse, fville=client_ville: 
                            self.app.invoice_actions.show_invoice_actions(fid, fnum, fclient, ftel, femail, faddr, fville))
            action_bar.add_widget(action_btn)
            card.add_widget(action_bar)
            
            self.list_layout.add_widget(card)
    
    def _update_rect(self, instance, value):
        if hasattr(instance, 'rect'):
            instance.rect.pos = instance.pos
            instance.rect.size = instance.size
    
    def go_back(self, instance):
        self.manager.current = 'dashboard'

# ============================================================================
# ÉCRAN NOUVELLE VENTE AVANCÉE
# ============================================================================

class NouvelleVenteAvanceeScreen(Screen):
    """Écran de création de vente avec gestion complète du panier"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client_id = None
        self.panier = []
        self.produits_data = []
        self.selected_item_index = -1
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.confirm_quit)
        header.add_widget(back_btn)
        header.add_widget(Label(text='NOUVELLE VENTE', font_size=18, bold=True))
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Formulaire avec défilement
        scroll = ScrollView(size_hint=(1, 0.7))
        form = BoxLayout(orientation='vertical', padding=10, spacing=10, size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        
        # Sélection client
        form.add_widget(Label(text='Client:', halign='left', bold=True, size_hint_y=None, height=30))
        self.client_spinner = Spinner(
            text='Choisir un client',
            values=[],
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.client_spinner)
        
        # Sélection produit
        form.add_widget(Label(text='Produit:', halign='left', bold=True, size_hint_y=None, height=30))
        self.produit_spinner = Spinner(
            text='Choisir un produit',
            values=[],
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.produit_spinner)
        
        # Quantité
        form.add_widget(Label(text='Quantité:', halign='left', bold=True, size_hint_y=None, height=30))
        self.quantite_input = TextInput(
            text='1',
            input_filter='int',
            multiline=False,
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.quantite_input)
        
        # Boutons d'action pour le panier
        btn_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        
        add_btn = Button(text='AJOUTER', background_color=(0.3, 0.7, 0.4, 1), bold=True)
        add_btn.bind(on_press=self.add_to_cart)
        btn_layout.add_widget(add_btn)
        
        modify_btn = Button(text='MODIFIER', background_color=(0.2, 0.6, 0.9, 1), bold=True)
        modify_btn.bind(on_press=self.prepare_modify_item)
        btn_layout.add_widget(modify_btn)
        
        remove_btn = Button(text='SUPPRIMER', background_color=(0.9, 0.3, 0.3, 1), bold=True)
        remove_btn.bind(on_press=self.remove_from_cart)
        btn_layout.add_widget(remove_btn)
        
        form.add_widget(btn_layout)
        
        # Liste du panier
        form.add_widget(Label(text='PANIER:', halign='left', bold=True, size_hint_y=None, height=30))
        
        self.cart_list = BoxLayout(orientation='vertical', size_hint_y=None, spacing=2)
        self.cart_list.bind(minimum_height=self.cart_list.setter('height'))
        form.add_widget(self.cart_list)
        
        # Mode de paiement
        form.add_widget(Label(text='Mode de paiement:', bold=True, size_hint_y=None, height=30))
        self.paiement_spinner = Spinner(
            text='Espèces',
            values=('Espèces', 'Carte bancaire', 'Virement', 'Chèque', 'Paiement mobile'),
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.paiement_spinner)
        
        # Statut
        form.add_widget(Label(text='Statut:', bold=True, size_hint_y=None, height=30))
        self.statut_spinner = Spinner(
            text='payée',
            values=('payée', 'impayée', 'annulée', 'en attente', 'partiellement payée'),
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.statut_spinner)
        
        # Montant payé
        form.add_widget(Label(text='Montant payé (Fbu):', bold=True, size_hint_y=None, height=30))
        self.montant_paye_input = TextInput(
            text='0',
            input_filter='float',
            multiline=False,
            size_hint=(1, None),
            height=40
        )
        self.montant_paye_input.bind(text=self.update_reste)
        form.add_widget(self.montant_paye_input)
        
        # Reste à payer
        form.add_widget(Label(text='Reste à payer:', bold=True, size_hint_y=None, height=30))
        self.reste_label = Label(
            text='0 Fbu',
            font_size=16,
            color=(0.8, 0.2, 0.2, 1),
            size_hint_y=None,
            height=30
        )
        form.add_widget(self.reste_label)
        
        # Total
        form.add_widget(Label(text='Total TTC:', bold=True, size_hint_y=None, height=30))
        self.total_label = Label(
            text='0 Fbu',
            font_size=20,
            bold=True,
            color=(0.2, 0.8, 0.2, 1),
            size_hint_y=None,
            height=40
        )
        form.add_widget(self.total_label)
        
        # Boutons d'action finaux
        final_btn_layout = BoxLayout(size_hint_y=None, height=50, spacing=5)
        
        cancel_btn = Button(text='ANNULER', background_color=(0.8, 0.2, 0.2, 1), bold=True)
        cancel_btn.bind(on_press=self.cancel_vente)
        final_btn_layout.add_widget(cancel_btn)
        
        validate_btn = Button(text='VALIDER', background_color=(0.2, 0.8, 0.2, 1), bold=True)
        validate_btn.bind(on_press=self.validate_vente)
        final_btn_layout.add_widget(validate_btn)
        
        form.add_widget(final_btn_layout)
        
        scroll.add_widget(form)
        layout.add_widget(scroll)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('NOUVEAU', 'nouvelle_vente')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        
        self.add_widget(layout)
    
    def on_enter(self):
        self.load_clients()
        self.load_produits()
        self.update_cart_display()
    
    def load_clients(self):
        app = App.get_running_app()
        db = app.db
        clients = db.get_clients()
        self.client_spinner.values = [f"{c[1]} (ID:{c[0]})" for c in clients]
    
    def load_produits(self):
        app = App.get_running_app()
        db = app.db
        tous_produits = db.get_produits()
        
        # ⭐ FILTRER : Garder seulement les produits avec stock > 0
        self.produits_data = [p for p in tous_produits if p[3] > 0]
        
        if self.produits_data:
            self.produit_spinner.values = [
                f"{p[1]} - {p[2]:,.0f} Fbu (Stock: {p[3]})"
                for p in self.produits_data
            ]
            self.produit_spinner.text = self.produit_spinner.values[0]
        else:
            self.produit_spinner.values = []
            self.produit_spinner.text = 'Aucun produit disponible'
            # Afficher un message d'alerte
            self.show_message("Information", "Aucun produit en stock disponible pour la vente")
    
    def add_to_cart(self, instance):
        """Ajoute un produit au panier (sans doublon)"""
        if self.produit_spinner.text == 'Choisir un produit' or not self.produit_spinner.text:
            self.show_message("Erreur", "Veuillez sélectionner un produit")
            return
        
        if self.produit_spinner.text == 'Aucun produit disponible':
            self.show_message("Erreur", "Aucun produit disponible")
            return
        
        try:
            idx = self.produit_spinner.values.index(self.produit_spinner.text)
        except ValueError:
            idx = 0
            self.produit_spinner.text = self.produit_spinner.values[0]
        
        produit = self.produits_data[idx]
        
        try:
            quantite = int(self.quantite_input.text)
            if quantite <= 0:
                quantite = 1
                self.quantite_input.text = '1'
        except:
            quantite = 1
            self.quantite_input.text = '1'
        
        stock_disponible = int(produit[3]) if produit[3] is not None else 0
        
        if stock_disponible < quantite:
            self.show_message("Stock insuffisant", f"Disponible: {stock_disponible}")
            return
        
        produit_id = produit[0]
        nom = produit[1]
        prix_ht_unitaire = float(produit[2]) if produit[2] else 0
        taux_tva = float(produit[5]) if len(produit) > 5 and produit[5] else 0
        
        total_ht = prix_ht_unitaire * quantite
        montant_tva = total_ht * (taux_tva / 100)
        total_ttc = total_ht + montant_tva
        
        # ⭐⭐⭐ VÉRIFIER SI LE PRODUIT EST DÉJÀ DANS LE PANIER ⭐⭐⭐
        for item in self.panier:
            if item['produit_id'] == produit_id:
                # Le produit existe déjà, on augmente la quantité
                item['quantite'] += quantite
                item['total_ht'] = item['prix_ht_unitaire'] * item['quantite']
                item['montant_tva'] = item['total_ht'] * (item['taux_tva'] / 100)
                item['total_ttc'] = item['total_ht'] + item['montant_tva']

                # ⭐⭐⭐ AJOUTER LE LOG ICI ⭐⭐⭐
                app = App.get_running_app()
                app.db.add_log(
                    app.user_data.get('username', 'Utilisateur'),
                    'produit',
                    'Vente',
                    f"Augmentation quantité: {quantite} x {nom} (total: {item['quantite']})"
                )
                
                self.show_message("Info", f"Quantité augmentée pour {nom}")
                self.update_cart_display()
                self.clear_product_selection()
                return
        
        # Si le produit n'existe pas dans le panier, on l'ajoute
        self.panier.append({
            'produit_id': produit_id,
            'nom': nom,
            'prix_ht_unitaire': prix_ht_unitaire,
            'quantite': quantite,
            'total_ht': total_ht,
            'taux_tva': taux_tva,
            'montant_tva': montant_tva,
            'total_ttc': total_ttc,
            'prix_ttc_unitaire': prix_ht_unitaire * (1 + taux_tva/100)
        })
        
        # ⭐⭐⭐ AJOUTER LE LOG ICI ⭐⭐⭐
        app = App.get_running_app()
        app.db.add_log(
            app.user_data.get('username', 'Utilisateur'),
            'produit',
            'Vente',
            f"Ajout de {quantite} x {nom} au panier"
        )        

        self.selected_item_index = -1
        self.update_cart_display()
        self.clear_product_selection()
    
    def prepare_modify_item(self, instance):
        if self.selected_item_index < 0 or self.selected_item_index >= len(self.panier):
            self.show_message("Erreur", "Sélectionnez d'abord un article dans le panier")
            return
        
        item = self.panier[self.selected_item_index]
        
        for i, p in enumerate(self.produits_data):
            if p[0] == item['produit_id']:
                self.produit_spinner.text = self.produit_spinner.values[i]
                break
        
        self.quantite_input.text = str(item['quantite'])
        
        del self.panier[self.selected_item_index]
        self.selected_item_index = -1
        self.update_cart_display()

    def remove_from_cart(self, instance):
        """Supprime l'article sélectionné du panier"""
        if self.selected_item_index < 0 or self.selected_item_index >= len(self.panier):
            self.show_message("Erreur", "Sélectionnez d'abord un article dans le panier")
            return
        
        del self.panier[self.selected_item_index]
        self.selected_item_index = -1
        self.update_cart_display()
    
    def update_cart_display(self):
        self.cart_list.clear_widgets()
        
        if not self.panier:
            self.cart_list.add_widget(Label(
                text='Panier vide',
                size_hint_y=None,
                height=40,
                color=(0.5, 0.5, 0.5, 1)
            ))
            self.total_label.text = '0 Fbu'
            self.update_reste()
            return
        
        total_ht = 0
        total_tva = 0
        total_ttc = 0
        
        for i, item in enumerate(self.panier):
            total_ht += item['total_ht']
            total_tva += item['montant_tva']
            total_ttc += item['total_ttc']
            
            item_frame = BoxLayout(orientation='vertical', size_hint_y=None, height=90)
            item_frame.item_index = i
            
            # Ligne 1: Nom et quantité
            line1 = BoxLayout()
            line1.add_widget(Label(
                text=item['nom'][:25],
                font_size=14,
                bold=(i == self.selected_item_index),
                color=(1, 1, 1, 1) if i == self.selected_item_index else (0.8, 0.8, 0.8, 1)
            ))
            line1.add_widget(Label(text=f"Qté: {item['quantite']}", font_size=12))
            item_frame.add_widget(line1)
            
            # Ligne 2: Prix HT et TVA
            line2 = BoxLayout()
            line2.add_widget(Label(
                text=f"HT: {item['total_ht']:,.0f} Fbu",
                font_size=12,
                color=(0.5, 0.5, 0.5, 1)
            ))
            if item['taux_tva'] > 0:
                line2.add_widget(Label(
                    text=f"TVA {item['taux_tva']}%: {item['montant_tva']:,.0f} Fbu",
                    font_size=12,
                    color=(1, 0.5, 0, 1)
                ))
            else:
                line2.add_widget(Label(text="TVA 0%", font_size=12, color=(0.5, 0.5, 0.5, 1)))
            item_frame.add_widget(line2)
            
            # Ligne 3: Prix TTC
            line3 = BoxLayout()
            line3.add_widget(Label(
                text=f"TTC: {item['total_ttc']:,.0f} Fbu",
                font_size=13,
                bold=True,
                color=(0.2, 0.8, 0.2, 1)
            ))
            line3.add_widget(Label(
                text=f"({item['prix_ttc_unitaire']:,.0f} Fbu/unité)",
                font_size=10,
                color=(0.5, 0.5, 0.5, 1)
            ))
            item_frame.add_widget(line3)
            
            item_frame.bind(on_touch_down=self.select_item)
            self.cart_list.add_widget(item_frame)
        
        # Totaux généraux
        total_frame = BoxLayout(orientation='vertical', size_hint_y=None, height=100)
        
        line_ht = BoxLayout()
        line_ht.add_widget(Label(text='Total HT:', font_size=13, bold=True))
        line_ht.add_widget(Label(text=f'{total_ht:,.0f} Fbu', font_size=13, bold=True))
        total_frame.add_widget(line_ht)
        
        line_tva = BoxLayout()
        line_tva.add_widget(Label(text='Total TVA:', font_size=13, bold=True, color=(1, 0.5, 0, 1)))
        line_tva.add_widget(Label(text=f'{total_tva:,.0f} Fbu', font_size=13, bold=True, color=(1, 0.5, 0, 1)))
        total_frame.add_widget(line_tva)
        
        line_ttc = BoxLayout()
        line_ttc.add_widget(Label(text='TOTAL TTC:', font_size=16, bold=True, color=(0.2, 0.8, 0.2, 1)))
        line_ttc.add_widget(Label(text=f'{total_ttc:,.0f} Fbu', font_size=18, bold=True, color=(0.2, 0.8, 0.2, 1)))
        total_frame.add_widget(line_ttc)
        
        self.cart_list.add_widget(total_frame)
        
        self.total_label.text = f'{total_ttc:,.0f} Fbu'
        self.update_reste()
    
    def select_item(self, instance, touch):
        if touch.button != 'left':
            return
        if not instance.collide_point(*touch.pos):
            return
        if hasattr(instance, 'item_index'):
            self.selected_item_index = instance.item_index
            self.update_cart_display()
    
    def clear_product_selection(self):
        if self.produit_spinner.values:
            self.produit_spinner.text = self.produit_spinner.values[0]
        self.quantite_input.text = '1'
    
    def update_reste(self, *args):
        total_ttc = sum(item['total_ttc'] for item in self.panier)
        try:
            montant_paye = float(self.montant_paye_input.text or 0)
        except:
            montant_paye = 0
        reste = total_ttc - montant_paye
        
        if reste < 0:
            self.reste_label.text = f"0 Fbu (trop perçu: {abs(reste):,.0f})"
            self.reste_label.color = (0.2, 0.8, 0.2, 1)
        else:
            self.reste_label.text = f"{reste:,.0f} Fbu"
            self.reste_label.color = (0.8, 0.2, 0.2, 1)
    
    def confirm_quit(self, instance=None):
        if self.panier:
            content = BoxLayout(orientation='vertical', spacing=10, padding=10)
            content.add_widget(Label(text='Le panier n\'est pas vide. Quitter quand même ?'))
            
            btn_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
            
            def do_quit(instance):
                popup.dismiss()
                self.manager.current = 'ventes'
            
            def do_cancel(instance):
                popup.dismiss()
            
            quit_btn = Button(text='OUI', background_color=(0.8, 0.2, 0.2, 1))
            quit_btn.bind(on_press=do_quit)
            btn_layout.add_widget(quit_btn)
            
            cancel_btn = Button(text='NON', background_color=(0.3, 0.3, 0.3, 1))
            cancel_btn.bind(on_press=do_cancel)
            btn_layout.add_widget(cancel_btn)
            
            content.add_widget(btn_layout)
            
            popup = Popup(title='Confirmation', content=content, size_hint=(0.8, 0.4))
            popup.open()
        else:
            self.manager.current = 'ventes'
    
    def cancel_vente(self, instance):
        if self.panier:
            content = BoxLayout(orientation='vertical', spacing=10, padding=10)
            content.add_widget(Label(text='Voulez-vous vraiment annuler cette vente ?'))
            
            btn_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
            
            def do_cancel(instance):
                self.panier = []
                self.selected_item_index = -1
                self.client_spinner.text = 'Choisir un client'
                self.clear_product_selection()
                self.montant_paye_input.text = '0'
                self.statut_spinner.text = 'payée'
                self.update_cart_display()
                popup.dismiss()
                self.manager.current = 'ventes'
            
            def do_keep(instance):
                popup.dismiss()
            
            cancel_btn = Button(text='ANNULER', background_color=(0.8, 0.2, 0.2, 1))
            cancel_btn.bind(on_press=do_cancel)
            btn_layout.add_widget(cancel_btn)
            
            keep_btn = Button(text='GARDER', background_color=(0.3, 0.7, 0.4, 1))
            keep_btn.bind(on_press=do_keep)
            btn_layout.add_widget(keep_btn)
            
            content.add_widget(btn_layout)
            
            popup = Popup(title='Confirmation', content=content, size_hint=(0.8, 0.4))
            popup.open()
        else:
            self.manager.current = 'ventes'
    
    def validate_vente(self, instance):
        if not self.panier:
            self.show_message("Erreur", "Le panier est vide")
            return
        
        # ⭐⭐⭐ CRUCIAL : Récupérer le statut AVANT toute opération ⭐⭐⭐
        statut = self.statut_spinner.text
        
        # ⭐⭐⭐ SI VENTE ANNULÉE, ON SAUTE TOUTE LA GESTION DE STOCK ⭐⭐⭐
        if statut == "annulée":
            print("⚠️⚠️⚠️ VENTE ANNULÉE - AUCUNE DIMINUTION DE STOCK ⚠️⚠️⚠️")
            
            app = App.get_running_app()
            
            # Récupérer l'ID client
            if self.client_spinner.text == 'Choisir un client':
                client_id = None
            else:
                try:
                    client_id = int(self.client_spinner.text.split('ID:')[1].rstrip(')'))
                except:
                    client_id = None
            
            # Calculer les totaux
            total_ht = sum(item['total_ht'] for item in self.panier)
            total_tva = sum(item['montant_tva'] for item in self.panier)
            total_ttc = sum(item['total_ttc'] for item in self.panier)
            
            # ⭐⭐⭐ POUR VENTE ANNULÉE : montant_paye = 0 ⭐⭐⭐
            montant_paye = 0
            reste_a_payer = total_ttc
            
            print(f"💰 Totaux pour vente ANNULÉE:")
            print(f"   total_ttc: {total_ttc}")
            print(f"   statut: {statut}")
            print(f"   montant_paye: {montant_paye}")
            print(f"   reste_a_payer: {reste_a_payer}")
            
            # Lignes pour stockage local
            lignes_local = []
            for item in self.panier:
                lignes_local.append({
                    'produit_id': item['produit_id'],
                    'quantite': item['quantite'],
                    'prix': item['prix_ttc_unitaire']
                })
            
            # Enregistrer en local SANS diminuer le stock
            facture_id, numero = app.db.add_facture(
                client_id, total_ttc, self.paiement_spinner.text, lignes_local,
                statut=statut, montant_paye=montant_paye
            )

            # ⭐⭐⭐ AJOUTER LE LOG ICI ⭐⭐⭐
            if facture_id:
                # Ajouter le log d'activité
                app.db.add_log(
                    app.user_data.get('username', 'Utilisateur'),
                    'vente',
                    'Factures',
                    f"Création facture {numero} - Total: {total_ttc:,.0f} Fbu - Statut: {statut}"
                )
                
                self.show_message("Succès", f"Facture {numero} créée")
            
            if facture_id:
                self.show_message("Succès", f"Facture annulée {numero} créée (stock inchangé)")
                
                # Envoi au serveur SANS mise à jour de stock
                if app.network and app.network.connected:
                    lignes_serveur = []
                    for item in self.panier:
                        lignes_serveur.append({
                            'produit_id': item['produit_id'],
                            'quantite': item['quantite'],
                            'prix_unitaire': item['prix_ht_unitaire'],
                            'taux_tva': item['taux_tva'],
                            'montant_tva': item['montant_tva'],
                            'total_ligne': item['total_ttc']
                        })
                    
                    facture_data = {
                        'numero': numero,
                        'client_id': client_id,
                        'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'total_ht': float(total_ht),
                        'total_tva': float(total_tva),
                        'total_ttc': float(total_ttc),
                        'statut': statut,
                        'mode_paiement': self.paiement_spinner.text,
                        'montant_paye': float(montant_paye),
                        'reste_a_payer': float(reste_a_payer),
                        'lignes': lignes_serveur
                    }
                    
                    # ⭐⭐⭐ Envoi direct ⭐⭐⭐
                    app.network.send_update('factures', 'insert', facture_data)
                    print(f"📤 Vente ANNULÉE envoyée au serveur (SANS mise à jour stock)")
                
                # Réinitialiser le panier
                self.panier = []
                self.selected_item_index = -1
                self.client_spinner.text = 'Choisir un client'
                self.clear_product_selection()
                self.montant_paye_input.text = '0'
                self.statut_spinner.text = 'payée'
                self.update_cart_display()
                
                self.manager.current = 'dashboard'
                Clock.schedule_once(lambda dt: self.manager.get_screen('dashboard').load_data(), 0.5)
            
            return  # ⭐⭐⭐ SORTIR ICI POUR NE PAS EXÉCUTER LE CODE DES VENTES NORMALES ⭐⭐⭐
        
        # ⭐⭐⭐ À PARTIR D'ICI, CODE POUR LES VENTES NON ANNULÉES ⭐⭐⭐
        print(f"✅ Vente NORMALE ({statut}) - Vérification et diminution du stock")
        
        # Vérifier le stock
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        stock_ok = True
        produits_a_synchroniser = []
        
        for item in self.panier:
            cursor.execute("SELECT quantite_stock, nom FROM produits WHERE id = ?", (item['produit_id'],))
            result = cursor.fetchone()
            if result:
                stock, nom = result
                if stock < item['quantite']:
                    stock_ok = False
                    self.show_message("Stock insuffisant", f"{nom}: disponible {stock}")
                    break
                else:
                    produits_a_synchroniser.append({
                        'id': item['produit_id'],
                        'nom': nom,
                        'ancien_stock': stock,
                        'nouveau_stock': stock - item['quantite'],
                        'quantite_vendue': item['quantite']
                    })
        
        conn.close()
        
        if not stock_ok:
            return
        
        # Récupérer l'ID client
        if self.client_spinner.text == 'Choisir un client':
            client_id = None
        else:
            try:
                client_id = int(self.client_spinner.text.split('ID:')[1].rstrip(')'))
            except:
                client_id = None
        
        # Calculer les totaux
        total_ht = sum(item['total_ht'] for item in self.panier)
        total_tva = sum(item['montant_tva'] for item in self.panier)
        total_ttc = sum(item['total_ttc'] for item in self.panier)
        
        print(f"💰 Totaux calculés:")
        print(f"   total_ht: {total_ht}")
        print(f"   total_tva: {total_tva}")
        print(f"   total_ttc: {total_ttc}")
        print(f"   statut: {statut}")
        print(f"   mode_paiement: {self.paiement_spinner.text}")
        
        # ⭐⭐⭐ CORRECTION : Ajuster montant_paye selon le statut ⭐⭐⭐
        try:
            montant_paye = float(self.montant_paye_input.text or 0)
        except:
            montant_paye = 0
        
        # Gérer les différents statuts
        if statut == "payée":
            montant_paye = total_ttc
            reste_a_payer = 0
            print(f"   ✅ Statut payée: montant_paye forcé à {montant_paye}")
            
        elif statut == "partiellement payée":
            if montant_paye > total_ttc:
                montant_paye = total_ttc
                reste_a_payer = 0
                print(f"   ⚠️ Montant payé > total, ajusté à {montant_paye}")
            else:
                reste_a_payer = total_ttc - montant_paye
            print(f"   💰 Paiement partiel: {montant_paye} payé, reste {reste_a_payer}")
            
        elif statut == "en attente":
            montant_paye = 0
            reste_a_payer = total_ttc
            print(f"   ⏳ En attente: montant_paye = 0")
            
        elif statut == "impayée":
            montant_paye = 0
            reste_a_payer = total_ttc
            print(f"   ❌ Impayée: montant_paye = 0")
            
        else:
            reste_a_payer = total_ttc - montant_paye
            print(f"   ℹ️ Statut {statut}: montant_paye = {montant_paye}, reste = {reste_a_payer}")
        
        # Lignes pour le serveur
        lignes_serveur = []
        for item in self.panier:
            lignes_serveur.append({
                'produit_id': item['produit_id'],
                'quantite': item['quantite'],
                'prix_unitaire': item['prix_ht_unitaire'],
                'taux_tva': item['taux_tva'],
                'montant_tva': item['montant_tva'],
                'total_ligne': item['total_ttc']
            })
        
        # Lignes pour stockage local
        lignes_local = []
        for item in self.panier:
            lignes_local.append({
                'produit_id': item['produit_id'],
                'quantite': item['quantite'],
                'prix': item['prix_ttc_unitaire']
            })
        
        # Enregistrer en local avec les valeurs corrigées
        facture_id, numero = app.db.add_facture(
            client_id, total_ttc, self.paiement_spinner.text, lignes_local,
            statut=statut, montant_paye=montant_paye
        )
        
        if facture_id:

            # ⭐ AJOUTER LE LOG
            try:
                app.db.add_log(
                    app.user_data.get('username', 'Utilisateur') if app.user_data else 'Utilisateur',
                    'vente',
                    'Factures',
                    f"Création facture {numero} - Total: {total_ttc:,.0f} Fbu - Statut: {statut}"
                )
            except Exception as e:
                print(f"⚠️ Erreur ajout log vente: {e}")
            
            self.show_message("Succès", f"Facture {numero} créée")            
            
            if app.network and app.network.connected:
                facture_data = {
                    'numero': numero,
                    'client_id': client_id,
                    'date': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'total_ht': float(total_ht),
                    'total_tva': float(total_tva),
                    'total_ttc': float(total_ttc),
                    'statut': statut,
                    'mode_paiement': self.paiement_spinner.text,
                    'montant_paye': float(montant_paye),
                    'reste_a_payer': float(reste_a_payer),
                    'lignes': lignes_serveur
                }
                
                print(f"\n{'='*60}")
                print(f"🔍 CONTENU DE facture_data AVANT ENVOI")
                print(f"{'='*60}")
                print(f"   numero: {facture_data['numero']} ({type(facture_data['numero'])})")
                print(f"   client_id: {facture_data['client_id']} ({type(facture_data['client_id'])})")
                print(f"   total_ht: {facture_data['total_ht']} ({type(facture_data['total_ht'])})")
                print(f"   total_tva: {facture_data['total_tva']} ({type(facture_data['total_tva'])})")
                print(f"   total_ttc: {facture_data['total_ttc']} ({type(facture_data['total_ttc'])})")
                print(f"   statut: {facture_data['statut']} ({type(facture_data['statut'])})")
                print(f"   mode_paiement: {facture_data['mode_paiement']} ({type(facture_data['mode_paiement'])})")
                print(f"   montant_paye: {facture_data['montant_paye']} ({type(facture_data['montant_paye'])})")
                print(f"   reste_a_payer: {facture_data['reste_a_payer']} ({type(facture_data['reste_a_payer'])})")
                print(f"{'='*60}\n")
                
                # ⭐⭐⭐ Envoi direct (rapide) ⭐⭐⭐
                app.network.send_update('factures', 'insert', facture_data)
                
                # ⭐⭐⭐ Envoi des mises à jour de stock ⭐⭐⭐
                for produit in produits_a_synchroniser:
                    # Diminuer le stock en base de données locale
                    conn = app.db.get_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        UPDATE produits 
                        SET quantite_stock = quantite_stock - ? 
                        WHERE id = ?
                    """, (produit['quantite_vendue'], produit['id']))
                    conn.commit()
                    conn.close()
                    
                    # Envoyer au serveur
                    stock_data = {
                        'id': produit['id'],
                        'nom': produit['nom'],
                        'ancien_stock': produit['ancien_stock'],
                        'nouveau_stock': produit['nouveau_stock'],
                        'quantite_vendue': produit['quantite_vendue']
                    }
                    app.network.send_update('produits', 'update_stock', stock_data)
                    print(f"📤 Envoi mise à jour stock: {produit['nom']} -> {produit['nouveau_stock']}")
            
            # Réinitialiser le panier
            self.panier = []
            self.selected_item_index = -1
            self.client_spinner.text = 'Choisir un client'
            self.clear_product_selection()
            self.montant_paye_input.text = '0'
            self.statut_spinner.text = 'payée'
            self.update_cart_display()
            
            self.manager.current = 'dashboard'
            Clock.schedule_once(lambda dt: self.manager.get_screen('dashboard').load_data(), 0.5)
        
        
    def show_message(self, title, message):
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 2)
        
# ============================================================================
# ÉCRAN PERMISSIONS
# ============================================================================        

class PermissionManager:
    """Gestionnaire des permissions"""
    
    # Définition des modules
    MODULES = {
        'dashboard': ['view'],
        'clients': ['view', 'add', 'edit', 'delete'],
        'products': ['view', 'add', 'edit', 'delete', 'stock'],
        'invoices': ['view', 'add', 'edit', 'delete', 'print'],
        'stock': ['view', 'adjust'],
        'reports': ['view', 'generate'],
        'users': ['view', 'add', 'edit', 'delete'],
        'settings': ['view', 'edit']
    }
    
    @staticmethod
    def has_permission(user_data, module, action):
        """Vérifie si l'utilisateur a une permission"""
        if not user_data:
            return False
        
        # Admin a tous les droits
        if user_data.get('role') == 'admin':
            return True
        
        permissions = user_data.get('permissions', {})
        if module not in permissions:
            return False
        
        return action in permissions.get(module, [])
    
    @staticmethod
    def get_default_permissions(role):
        """Retourne les permissions par défaut selon le rôle"""
        default_perms = {
            'admin': {
                'dashboard': ['view'],
                'clients': ['view', 'add', 'edit', 'delete'],
                'products': ['view', 'add', 'edit', 'delete', 'stock'],
                'invoices': ['view', 'add', 'edit', 'delete', 'print'],
                'stock': ['view', 'adjust'],
                'reports': ['view', 'generate'],
                'users': ['view', 'add', 'edit', 'delete'],
                'settings': ['view', 'edit']
            },
            'Gérant': {
                'dashboard': ['view'],
                'clients': ['view', 'add', 'edit', 'delete'],
                'products': ['view', 'add', 'edit', 'delete', 'stock'],
                'invoices': ['view', 'add', 'edit', 'delete', 'print'],
                'stock': ['view', 'adjust'],
                'reports': ['view', 'generate'],
                'users': ['view'],
                'settings': ['view']
            },
            'Responsable Stock': {
                'dashboard': ['view'],
                'clients': ['view'],
                'products': ['view', 'add', 'edit', 'stock'],
                'invoices': ['view'],
                'stock': ['view', 'adjust'],
                'reports': ['view'],
                'users': []
            },
            'Vendeur': {
                'dashboard': ['view'],
                'clients': ['view', 'add', 'edit'],
                'products': ['view'],
                'invoices': ['view', 'add', 'print'],
                'stock': ['view'],
                'reports': ['view'],
                'users': []
            },
            'Caissier': {
                'dashboard': ['view'],
                'clients': ['view'],
                'products': ['view'],
                'invoices': ['view', 'add', 'print'],
                'stock': ['view'],
                'reports': [],
                'users': []
            },
            'viewer': {
                'dashboard': ['view'],
                'clients': ['view'],
                'products': ['view'],
                'invoices': ['view'],
                'stock': ['view'],
                'reports': [],
                'users': []
            }
        }
        return default_perms.get(role, {})
        
class UsersManagementScreen(Screen):
    """Écran de gestion des utilisateurs"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.all_users = []
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='GESTION UTILISATEURS', font_size=18, bold=True))
        add_btn = Button(text='+', size_hint=(0.15, 1), background_color=(0.2, 0.7, 0.3, 1))
        add_btn.bind(on_press=self.add_user)
        header.add_widget(add_btn)
        layout.add_widget(header)
        
        # Liste des utilisateurs
        self.scroll = ScrollView()
        self.list_layout = BoxLayout(orientation='vertical', spacing=8, padding=10, size_hint_y=None)
        self.list_layout.bind(minimum_height=self.list_layout.setter('height'))
        self.scroll.add_widget(self.list_layout)
        layout.add_widget(self.scroll)
        
        self.add_widget(layout)
    
    def on_enter(self):
        self.load_users()
    
    def load_users(self):
        """Charge les utilisateurs"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT id, username, full_name, role, email, is_active, created_at, last_login
                FROM users
                ORDER BY role, username
            """)
            self.all_users = cursor.fetchall()
            self.display_users(self.all_users)
        except Exception as e:
            print(f"❌ Erreur load_users: {e}")
        finally:
            conn.close()
    
    def display_users(self, users):
        """Affiche les utilisateurs"""
        self.list_layout.clear_widgets()
        
        if not users:
            self.list_layout.add_widget(Label(
                text='Aucun utilisateur',
                font_size=14,
                color=(0.5, 0.5, 0.5, 1),
                size_hint_y=None,
                height=100
            ))
            return
        
        for u in users:
            user_id = u[0]
            username = u[1]
            full_name = u[2] or username
            role = u[3]
            email = u[4] or 'N/A'
            is_active = u[5]
            
            # Carte simple
            card = BoxLayout(orientation='vertical', size_hint_y=None, height=100, padding=10, spacing=5)
            with card.canvas.before:
                Color(0.95, 0.95, 0.95, 1)
                card.rect = RoundedRectangle(pos=card.pos, size=card.size, radius=[dp(10)])
            card.bind(pos=self._update_rect, size=self._update_rect)
            
            # Nom et rôle
            line1 = BoxLayout()
            line1.add_widget(Label(text=full_name, font_size=14, bold=True))
            line1.add_widget(Label(text=role.upper(), font_size=11, color=(0.5, 0.5, 0.5, 1)))
            card.add_widget(line1)
            
            # Username et email
            line2 = BoxLayout()
            line2.add_widget(Label(text=f"@{username}", font_size=11, color=(0.5, 0.5, 0.5, 1)))
            line2.add_widget(Label(text=email, font_size=10, color=(0.5, 0.5, 0.5, 1)))
            card.add_widget(line2)
            
            # Boutons
            line3 = BoxLayout(size_hint_y=None, height=35, spacing=5)
            
            edit_btn = Button(text="Modifier", size_hint_x=0.3, font_size=11, background_color=(0.2, 0.6, 0.9, 1))
            edit_btn.bind(on_press=lambda x, uid=user_id: self.edit_user(uid))
            line3.add_widget(edit_btn)
            
            if user_id != 1:  # Ne pas supprimer l'admin principal
                delete_btn = Button(text="Supprimer", size_hint_x=0.3, font_size=11, background_color=(0.8, 0.2, 0.2, 1))
                delete_btn.bind(on_press=lambda x, uid=user_id, uname=username: self.delete_user(uid, uname))
                line3.add_widget(delete_btn)
            
            card.add_widget(line3)
            
            self.list_layout.add_widget(card)
    
    def _update_rect(self, instance, value):
        if hasattr(instance, 'rect'):
            instance.rect.pos = instance.pos
            instance.rect.size = instance.size
    
    def add_user(self, instance):
        """Ajoute un utilisateur"""
        print("➕ Ajout d'un utilisateur")
        self.manager.current = 'user_form'
        self.manager.get_screen('user_form').set_mode('add')
    
    def edit_user(self, user_id):
        self.manager.current = 'user_form'
        self.manager.get_screen('user_form').set_mode('edit', user_id)
    
    def delete_user(self, user_id, username):
        """Supprime un utilisateur"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(text=f"Supprimer {username} ?", font_size=14))
        
        buttons = BoxLayout(size_hint_y=None, height=50, spacing=10)
        
        def do_delete(instance):
            popup.dismiss()
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
                conn.commit()
                self.load_users()
            except Exception as e:
                print(f"❌ Erreur: {e}")
            finally:
                conn.close()
        
        def cancel(instance):
            popup.dismiss()
        
        delete_btn = Button(text="SUPPRIMER", background_color=(0.8, 0.2, 0.2, 1))
        delete_btn.bind(on_press=do_delete)
        buttons.add_widget(delete_btn)
        
        cancel_btn = Button(text="ANNULER", background_color=(0.3, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=cancel)
        buttons.add_widget(cancel_btn)
        
        content.add_widget(buttons)
        
        popup = Popup(title="Confirmation", content=content, size_hint=(0.8, 0.4))
        popup.open()
    
    def go_back(self, instance):
        """Retourne au tableau de bord"""
        print("🔙 Retour au dashboard")
        self.manager.current = 'dashboard'  # ⭐ Changer 'main' en 'dashboard'
        
        
class UserFormScreen(Screen):
    """Formulaire utilisateur - Version avec nouveaux rôles"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mode = 'add'
        self.user_id = None
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical', spacing=10, padding=10)
        
        # En-tête
        header = BoxLayout(size_hint_y=0.1)
        back_btn = Button(text='GO BACK', size_hint_x=0.15, background_color=(0.5, 0.5, 0.5, 1))
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        self.title_label = Label(text="", font_size=18, bold=True)
        header.add_widget(self.title_label)
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Formulaire avec ScrollView
        scroll = ScrollView()
        form = BoxLayout(orientation='vertical', spacing=10, size_hint_y=None, padding=10)
        form.bind(minimum_height=form.setter('height'))
        
        # ⭐ Nom d'utilisateur
        self.username_input = TextInput(
            hint_text="Nom d'utilisateur *",
            multiline=False,
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            foreground_color=(0, 0, 0, 1),
            cursor_color=(0, 0, 0, 1),
            background_normal='',
            font_size=14
        )
        form.add_widget(self.username_input)
        
        # ⭐ Mot de passe
        self.password_input = TextInput(
            hint_text="Mot de passe",
            password=True,
            multiline=False,
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            foreground_color=(0, 0, 0, 1),
            cursor_color=(0, 0, 0, 1),
            background_normal='',
            font_size=14
        )
        form.add_widget(self.password_input)
        
        # ⭐ Confirmation
        self.confirm_input = TextInput(
            hint_text="Confirmer le mot de passe",
            password=True,
            multiline=False,
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            foreground_color=(0, 0, 0, 1),
            cursor_color=(0, 0, 0, 1),
            background_normal='',
            font_size=14
        )
        form.add_widget(self.confirm_input)
        
        # ⭐ Nom complet
        self.fullname_input = TextInput(
            hint_text="Nom complet",
            multiline=False,
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            foreground_color=(0, 0, 0, 1),
            cursor_color=(0, 0, 0, 1),
            background_normal='',
            font_size=14
        )
        form.add_widget(self.fullname_input)
        
        # ⭐ Email
        self.email_input = TextInput(
            hint_text="Email",
            multiline=False,
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            foreground_color=(0, 0, 0, 1),
            cursor_color=(0, 0, 0, 1),
            background_normal='',
            font_size=14
        )
        form.add_widget(self.email_input)
        
        # ⭐ Rôle - avec les nouveaux rôles
        role_label = Label(text="Rôle:", size_hint_y=None, height=30, font_size=14, bold=True, color=(1, 1, 1, 1))
        form.add_widget(role_label)
        self.role_spinner = Spinner(
            text='Vendeur',
            values=['admin', 'Gérant', 'Responsable Stock', 'Vendeur', 'Caissier', 'viewer'],
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            color=(0.8, 0.6, 0, 1),
            font_size=14
        )
        form.add_widget(self.role_spinner)
        
        # ⭐ Statut
        status_label = Label(text="Statut:", size_hint_y=None, height=30, font_size=14, bold=True, color=(1, 1, 1, 1))
        form.add_widget(status_label)
        self.status_spinner = Spinner(
            text='Actif',
            values=['Actif', 'Inactif'],
            height=50,
            size_hint_y=None,
            background_color=(1, 1, 1, 1),
            color=(0.2, 0.8, 0.2, 1),
            font_size=14
        )
        form.add_widget(self.status_spinner)
        
        scroll.add_widget(form)
        layout.add_widget(scroll)
        
        # Boutons
        buttons = BoxLayout(size_hint_y=None, height=60, spacing=10)
        save_btn = Button(
            text="ENREGISTRER",
            background_color=(0.2, 0.7, 0.3, 1),
            font_size=14,
            bold=True
        )
        save_btn.bind(on_press=self.save_user)
        buttons.add_widget(save_btn)
        
        cancel_btn = Button(
            text="ANNULER",
            background_color=(0.8, 0.3, 0.3, 1),
            font_size=14,
            bold=True
        )
        cancel_btn.bind(on_press=self.go_back)
        buttons.add_widget(cancel_btn)
        
        layout.add_widget(buttons)
        
        self.add_widget(layout)
    
    def set_mode(self, mode, user_id=None):
        self.mode = mode
        self.user_id = user_id
        
        if mode == 'add':
            self.title_label.text = "NOUVEL UTILISATEUR"
            self.clear_form()
        else:
            self.title_label.text = "MODIFIER UTILISATEUR"
            self.load_user(user_id)
    
    def clear_form(self):
        self.username_input.text = ""
        self.password_input.text = ""
        self.confirm_input.text = ""
        self.fullname_input.text = ""
        self.email_input.text = ""
        self.role_spinner.text = "Vendeur"
        self.status_spinner.text = "Actif"
        self.username_input.disabled = False
    
    def load_user(self, user_id):
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT username, full_name, email, role, is_active FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            
            if user:
                self.username_input.text = user[0]
                self.fullname_input.text = user[1] or ""
                self.email_input.text = user[2] or ""
                self.role_spinner.text = user[3]
                self.status_spinner.text = "Actif" if user[4] == 1 else "Inactif"
                self.username_input.disabled = True
        except Exception as e:
            print(f"❌ Erreur load_user: {e}")
        finally:
            conn.close()
    
    def save_user(self, instance):
        username = self.username_input.text.strip()
        if not username:
            self.show_message("Erreur", "Nom d'utilisateur obligatoire")
            return
        
        password = self.password_input.text
        confirm = self.confirm_input.text
        
        if self.mode == 'add':
            if not password:
                self.show_message("Erreur", "Mot de passe obligatoire")
                return
            if password != confirm:
                self.show_message("Erreur", "Mots de passe différents")
                return
        
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        try:
            import hashlib
            import json
            from datetime import datetime
            import uuid
            
            hashed = hashlib.sha256(password.encode()).hexdigest() if password else None
            is_active = 1 if self.status_spinner.text == "Actif" else 0
            role = self.role_spinner.text
            user_uuid = str(uuid.uuid4())
            
            # Permissions par défaut selon le rôle
            permissions = PermissionManager.get_default_permissions(role)
            
            if self.mode == 'add':
                cursor.execute('''
                    INSERT INTO users (username, password, full_name, email, role, is_active, created_at, permissions, uuid)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (username, hashed, self.fullname_input.text, self.email_input.text,
                      role, is_active, datetime.now().isoformat(), json.dumps(permissions), user_uuid))
                
                user_id = cursor.lastrowid
                message = "Utilisateur ajouté avec succès"
                
                # ⭐ SYNCHRONISATION VERS LE SERVEUR
                if app.network and app.network.connected:
                    user_data = {
                        'id': user_id,
                        'username': username,
                        'password': hashed,
                        'full_name': self.fullname_input.text,
                        'email': self.email_input.text,
                        'role': role,
                        'is_active': is_active,
                        'created_at': datetime.now().isoformat(),
                        'permissions': json.dumps(permissions),
                        'uuid': user_uuid
                    }
                    print(f"📤 Envoi utilisateur au serveur: {username} (rôle: {role})")
                    result = app.network.send_update('users', 'insert', user_data)
                    print(f"   Résultat: {result}")
                else:
                    print(f"⚠️ Non connecté au serveur, utilisateur {username} non synchronisé")
            
            else:
                # Mise à jour
                if password:
                    cursor.execute('''
                        UPDATE users SET full_name=?, email=?, role=?, is_active=?, password=?, permissions=?
                        WHERE id=?
                    ''', (self.fullname_input.text, self.email_input.text, role, is_active, hashed,
                          json.dumps(permissions), self.user_id))
                else:
                    cursor.execute('''
                        UPDATE users SET full_name=?, email=?, role=?, is_active=?, permissions=?
                        WHERE id=?
                    ''', (self.fullname_input.text, self.email_input.text, role, is_active,
                          json.dumps(permissions), self.user_id))
                
                message = "Utilisateur modifié avec succès"
                
                # ⭐ SYNCHRONISATION DE LA MODIFICATION
                if app.network and app.network.connected:
                    user_data = {
                        'id': self.user_id,
                        'username': username,
                        'full_name': self.fullname_input.text,
                        'email': self.email_input.text,
                        'role': role,
                        'is_active': is_active,
                        'permissions': json.dumps(permissions)
                    }
                    if password:
                        user_data['password'] = hashed
                    
                    print(f"📤 Envoi modification utilisateur: {username} (rôle: {role})")
                    result = app.network.send_update('users', 'update', user_data)
                    print(f"   Résultat: {result}")
            
            conn.commit()
            self.show_message("Succès", message)
            
            # Rafraîchir la liste des utilisateurs
            users_screen = self.manager.get_screen('users')
            if hasattr(users_screen, 'load_users'):
                users_screen.load_users()
            
            self.go_back(None)
            
        except sqlite3.IntegrityError:
            self.show_message("Erreur", "Nom d'utilisateur déjà utilisé")
            conn.rollback()
        except Exception as e:
            print(f"❌ Erreur: {e}")
            import traceback
            traceback.print_exc()
            self.show_message("Erreur", str(e))
            conn.rollback()
        finally:
            conn.close()
    
    def show_message(self, title, message):
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message, font_size=14))
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 2)
    
    def go_back(self, instance):
        self.manager.current = 'users'
    
# ============================================================================
# ÉCRAN de statistiques avancées
# ============================================================================
class StatistiquesAvanceesScreen(Screen):
    """Écran des statistiques avancées avec graphiques"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()
        # ⭐ Charger les données après un petit délai
        from kivy.clock import Clock
        Clock.schedule_once(lambda dt: self.load_all_stats(), 0.5)
    
    def build_ui(self):
        from kivy.uix.image import Image as KivyImage
        
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.08), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='STATISTIQUES AVANCÉES', font_size=14, bold=True))
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Onglets
        tabs = BoxLayout(orientation='vertical')
        
        # Barre d'onglets
        tab_bar = BoxLayout(size_hint_y=None, height=40, spacing=2)
        self.tab_buttons = {}
        
        tabs_list = [
            ('VENTES', 'ventes'),
            ('PRODUITS', 'produits'),
            ('CLIENTS', 'clients'),
            ('TENDANCES', 'tendances')
        ]
        
        for text, name in tabs_list:
            btn = Button(text=text, font_size=12, background_color=(0.3, 0.3, 0.4, 1))
            btn.bind(on_press=lambda x, n=name: self.switch_tab(n))
            tab_bar.add_widget(btn)
            self.tab_buttons[name] = btn
        
        layout.add_widget(tab_bar)
        
        # Contenu des onglets (ScrollView)
        self.scroll = ScrollView()
        self.content = BoxLayout(orientation='vertical', size_hint_y=None, spacing=10, padding=10)
        self.content.bind(minimum_height=self.content.setter('height'))
        self.scroll.add_widget(self.content)
        layout.add_widget(self.scroll)
        
        # Initialiser les conteneurs d'onglets
        self.tabs = {
            'ventes': BoxLayout(orientation='vertical', size_hint_y=None),
            'produits': BoxLayout(orientation='vertical', size_hint_y=None),
            'clients': BoxLayout(orientation='vertical', size_hint_y=None),
            'tendances': BoxLayout(orientation='vertical', size_hint_y=None)
        }
        
        for tab in self.tabs.values():
            tab.bind(minimum_height=tab.setter('height'))
        
        self.current_tab = 'ventes'
        
        # ⭐ NE PAS APPELER switch_tab ICI, juste initialiser l'affichage
        # Afficher directement l'onglet ventes
        self.content.clear_widgets()
        self.content.add_widget(self.tabs['ventes'])
        
        # Mettre à jour l'apparence du bouton VENTES
        for name, btn in self.tab_buttons.items():
            if name == 'ventes':
                btn.background_color = (0.2, 0.6, 0.9, 1)
            else:
                btn.background_color = (0.3, 0.3, 0.4, 1)
        
        self.add_widget(layout)
    
    def switch_tab(self, tab_name):
        """Change d'onglet - ⭐ DOIT ÊTRE DÉFINIE AVANT D'ÊTRE UTILISÉE"""
        self.current_tab = tab_name
        
        # Mettre à jour l'apparence des boutons
        for name, btn in self.tab_buttons.items():
            if name == tab_name:
                btn.background_color = (0.2, 0.6, 0.9, 1)
            else:
                btn.background_color = (0.3, 0.3, 0.4, 1)
        
        # Afficher le contenu de l'onglet
        self.content.clear_widgets()
        self.content.add_widget(self.tabs[tab_name])
    
    # ⭐ Le reste des méthodes (load_all_stats, get_connection, load_ventes_stats, etc.)
    # doivent être définies APRÈS switch_tab ou dans n'importe quel ordre
    # tant qu'elles sont définies avant d'être appelées
    
    def load_all_stats(self):
        """Charge toutes les statistiques"""
        self.load_ventes_stats()
        self.load_produits_stats()
        self.load_clients_stats()
        self.load_tendances_stats()
    
    def get_connection(self):
        """Obtient une connexion à la base de données"""
        app = App.get_running_app()
        if hasattr(app, 'db') and app.db:
            return app.db.get_connection()
        return None
    
    def load_ventes_stats(self):
        """Charge les statistiques de ventes - VERSION CORRIGÉE"""
        from kivy.uix.image import Image as KivyImage
        
        conn = self.get_connection()
        if not conn:
            self.tabs['ventes'].add_widget(Label(text="❌ Impossible de se connecter à la base de données", color=(1,0,0,1)))
            return
        
        cursor = conn.cursor()
        
        try:
            # Vider l'onglet
            self.tabs['ventes'].clear_widgets()
            
            # ⭐ Vérifier s'il y a des factures
            cursor.execute("SELECT COUNT(*) FROM factures")
            total_factures = cursor.fetchone()[0]
            
            if total_factures == 0:
                self.tabs['ventes'].add_widget(Label(text="📊 Aucune vente enregistrée", color=(0.5,0.5,0.5,1), size_hint_y=None, height=50))
                return
            
            # 1. Évolution mensuelle
            cursor.execute("""
                SELECT 
                    strftime('%Y-%m', date) as mois,
                    COUNT(*) as nb_factures,
                    COALESCE(SUM(total_ttc), 0) as ca
                FROM factures
                WHERE date IS NOT NULL
                GROUP BY strftime('%Y-%m', date)
                ORDER BY mois DESC
                LIMIT 6
            """)
            data = cursor.fetchall()
            
            if data and len(data) > 0:
                mois = [d[0][5:] if d[0] and len(d[0]) >= 7 else d[0] for d in reversed(data)]
                ca = [float(d[2]) if d[2] else 0 for d in reversed(data)]
                nb = [d[1] if d[1] else 0 for d in reversed(data)]
                
                # Graphique CA mensuel
                if max(ca) > 0:
                    self.add_graphique(self.tabs['ventes'], "Évolution du CA mensuel", mois, ca, 'CA (Fbu)')
                else:
                    self.tabs['ventes'].add_widget(Label(text="Aucune donnée CA disponible", size_hint_y=None, height=30))
                
                # Graphique nombre de factures
                if max(nb) > 0:
                    self.add_bar_graphique(self.tabs['ventes'], "Nombre de factures par mois", mois, nb, 'Factures')
            else:
                self.tabs['ventes'].add_widget(Label(text="Aucune donnée mensuelle", size_hint_y=None, height=30))
            
            # 2. KPI globaux
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_factures,
                    COALESCE(SUM(total_ttc), 0) as ca_total,
                    COALESCE(AVG(total_ttc), 0) as panier_moyen,
                    COALESCE(MAX(total_ttc), 0) as max_facture
                FROM factures
            """)
            stats = cursor.fetchone()
            
            if stats:
                self.add_kpi_cards(self.tabs['ventes'], {
                    'Total factures': f"{stats[0]}",
                    'CA total': f"{stats[1]:,.0f} Fbu",
                    'Panier moyen': f"{stats[2]:,.0f} Fbu",
                    'Max facture': f"{stats[3]:,.0f} Fbu"
                })
            
        except Exception as e:
            print(f"❌ Erreur load_ventes_stats: {e}")
            import traceback
            traceback.print_exc()
            self.tabs['ventes'].clear_widgets()
            self.tabs['ventes'].add_widget(Label(text=f"Erreur: {str(e)[:50]}", color=(1,0,0,1), size_hint_y=None, height=50))
        finally:
            conn.close()
    
    def load_produits_stats(self):
        """Charge les statistiques de produits - VERSION CORRIGÉE"""
        from kivy.uix.image import Image as KivyImage
        
        conn = self.get_connection()
        if not conn:
            self.tabs['produits'].add_widget(Label(text="❌ Impossible de se connecter", color=(1,0,0,1)))
            return
        
        cursor = conn.cursor()
        
        try:
            self.tabs['produits'].clear_widgets()
            
            # ⭐ Vérifier s'il y a des produits vendus
            cursor.execute("SELECT COUNT(*) FROM lignes_facture")
            total_lignes = cursor.fetchone()[0]
            
            if total_lignes == 0:
                self.tabs['produits'].add_widget(Label(text="📦 Aucun produit vendu", color=(0.5,0.5,0.5,1), size_hint_y=None, height=50))
            else:
                # 1. Top produits vendus
                cursor.execute("""
                    SELECT p.nom, SUM(lf.quantite) as qte_vendue, COALESCE(SUM(lf.total_ligne), 0) as total
                    FROM lignes_facture lf
                    JOIN produits p ON lf.produit_id = p.id
                    GROUP BY lf.produit_id
                    ORDER BY total DESC
                    LIMIT 5
                """)
                top_produits = cursor.fetchall()
                
                if top_produits:
                    noms = [p[0][:15] if p[0] else 'Inconnu' for p in top_produits]
                    valeurs = [float(p[2]) if p[2] else 0 for p in top_produits]
                    
                    if max(valeurs) > 0:
                        self.add_bar_graphique(self.tabs['produits'], "Top 5 produits vendus", 
                                               noms, valeurs, 'Chiffre d\'affaires (Fbu)')
                    else:
                        self.tabs['produits'].add_widget(Label(text="Aucune vente de produit", size_hint_y=None, height=30))
            
            # 2. KPI produits
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_produits,
                    SUM(CASE WHEN actif = 1 THEN 1 ELSE 0 END) as actifs,
                    SUM(CASE WHEN quantite_stock <= seuil_alerte AND quantite_stock > 0 THEN 1 ELSE 0 END) as alerte,
                    SUM(CASE WHEN quantite_stock <= 0 THEN 1 ELSE 0 END) as rupture
                FROM produits
            """)
            stats = cursor.fetchone()
            
            if stats:
                self.add_kpi_cards(self.tabs['produits'], {
                    'Total produits': f"{stats[0]}",
                    'Actifs': f"{stats[1]}",
                    'Alerte stock': f"{stats[2]}",
                    'Rupture': f"{stats[3]}"
                })
            
        except Exception as e:
            print(f"❌ Erreur load_produits_stats: {e}")
            import traceback
            traceback.print_exc()
            self.tabs['produits'].clear_widgets()
            self.tabs['produits'].add_widget(Label(text=f"Erreur: {str(e)[:50]}", color=(1,0,0,1)))
        finally:
            conn.close()
    
    def load_clients_stats(self):
        """Charge les statistiques de clients - VERSION CORRIGÉE"""
        from kivy.uix.image import Image as KivyImage
        
        conn = self.get_connection()
        if not conn:
            self.tabs['clients'].add_widget(Label(text="❌ Impossible de se connecter", color=(1,0,0,1)))
            return
        
        cursor = conn.cursor()
        
        try:
            self.tabs['clients'].clear_widgets()
            
            # ⭐ Vérifier s'il y a des clients
            cursor.execute("SELECT COUNT(*) FROM clients")
            total_clients = cursor.fetchone()[0]
            
            if total_clients == 0:
                self.tabs['clients'].add_widget(Label(text="👥 Aucun client enregistré", color=(0.5,0.5,0.5,1), size_hint_y=None, height=50))
            else:
                # 1. Top clients par CA
                cursor.execute("""
                    SELECT c.nom, COUNT(f.id) as nb_factures, COALESCE(SUM(f.total_ttc), 0) as total
                    FROM clients c
                    LEFT JOIN factures f ON c.id = f.client_id
                    GROUP BY c.id
                    ORDER BY total DESC
                    LIMIT 5
                """)
                top_clients = cursor.fetchall()
                
                if top_clients:
                    noms = [c[0][:15] if c[0] else 'Anonyme' for c in top_clients]
                    valeurs = [float(c[2]) if c[2] else 0 for c in top_clients]
                    
                    if max(valeurs) > 0:
                        self.add_bar_graphique(self.tabs['clients'], "Top 5 clients", 
                                               noms, valeurs, 'CA (Fbu)')
                    else:
                        self.tabs['clients'].add_widget(Label(text="Aucun achat client", size_hint_y=None, height=30))
            
            # 2. KPI clients
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_clients,
                    COUNT(CASE WHEN statut = 'actif' OR statut IS NULL THEN 1 END) as actifs,
                    COUNT(CASE WHEN statut = 'inactif' THEN 1 END) as inactifs
                FROM clients
            """)
            stats = cursor.fetchone()
            
            if stats:
                self.add_kpi_cards(self.tabs['clients'], {
                    'Total clients': f"{stats[0]}",
                    'Actifs': f"{stats[1]}",
                    'Inactifs': f"{stats[2]}"
                })
            
        except Exception as e:
            print(f"❌ Erreur load_clients_stats: {e}")
            self.tabs['clients'].clear_widgets()
            self.tabs['clients'].add_widget(Label(text=f"Erreur: {str(e)[:50]}", color=(1,0,0,1)))
        finally:
            conn.close()
    
    def load_tendances_stats(self):
        """Charge les tendances statistiques - VERSION CORRIGÉE"""
        from kivy.uix.image import Image as KivyImage
        
        conn = self.get_connection()
        if not conn:
            self.tabs['tendances'].add_widget(Label(text="❌ Impossible de se connecter", color=(1,0,0,1)))
            return
        
        cursor = conn.cursor()
        
        try:
            self.tabs['tendances'].clear_widgets()
            
            # ⭐ Vérifier s'il y a des factures
            cursor.execute("SELECT COUNT(*) FROM factures")
            total_factures = cursor.fetchone()[0]
            
            if total_factures == 0:
                self.tabs['tendances'].add_widget(Label(text="📊 Aucune donnée de tendance", color=(0.5,0.5,0.5,1), size_hint_y=None, height=50))
                return
            
            # 1. Ventes par jour de semaine
            cursor.execute("""
                SELECT 
                    CASE CAST(strftime('%w', date) AS INTEGER)
                        WHEN 0 THEN 'Dimanche'
                        WHEN 1 THEN 'Lundi'
                        WHEN 2 THEN 'Mardi'
                        WHEN 3 THEN 'Mercredi'
                        WHEN 4 THEN 'Jeudi'
                        WHEN 5 THEN 'Vendredi'
                        WHEN 6 THEN 'Samedi'
                    END as jour,
                    COUNT(*) as nb_ventes,
                    COALESCE(SUM(total_ttc), 0) as ca
                FROM factures
                WHERE date IS NOT NULL
                GROUP BY strftime('%w', date)
                ORDER BY strftime('%w', date)
            """)
            jours = cursor.fetchall()
            
            if jours and len(jours) > 0:
                jour_noms = [j[0] for j in jours]
                ca_jour = [float(j[2]) if j[2] else 0 for j in jours]
                
                if max(ca_jour) > 0:
                    self.add_bar_graphique(self.tabs['tendances'], "CA par jour de semaine", 
                                           jour_noms, ca_jour, 'CA (Fbu)')
                else:
                    self.tabs['tendances'].add_widget(Label(text="Aucune donnée CA par jour", size_hint_y=None, height=30))
            
            # 2. Évolution sur 12 mois
            cursor.execute("""
                SELECT 
                    strftime('%Y-%m', date) as mois,
                    COUNT(*) as nb,
                    COALESCE(SUM(total_ttc), 0) as ca
                FROM factures
                WHERE date IS NOT NULL
                GROUP BY strftime('%Y-%m', date)
                ORDER BY mois DESC
                LIMIT 12
            """)
            evolution = cursor.fetchall()
            
            if evolution and len(evolution) > 0:
                mois = [e[0][5:] if e[0] and len(e[0]) >= 7 else e[0] for e in reversed(evolution)]
                ca = [float(e[2]) if e[2] else 0 for e in reversed(evolution)]
                
                if max(ca) > 0:
                    self.add_graphique(self.tabs['tendances'], "Évolution du CA sur 12 mois", mois, ca, 'CA (Fbu)')
                else:
                    self.tabs['tendances'].add_widget(Label(text="Aucune donnée CA mensuel", size_hint_y=None, height=30))
            
        except Exception as e:
            print(f"❌ Erreur load_tendances_stats: {e}")
            import traceback
            traceback.print_exc()
            self.tabs['tendances'].clear_widgets()
            self.tabs['tendances'].add_widget(Label(text=f"Erreur: {str(e)[:50]}", color=(1,0,0,1)))
        finally:
            conn.close()
    
    def add_graphique(self, parent, titre, labels, valeurs, unite):
        """Ajoute un graphique en ligne"""
        from kivy.uix.image import Image as KivyImage
        from kivy.core.image import Image as CoreImage
        from kivy.graphics.texture import Texture
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import io
        
        try:
            # Vérifier qu'il y a des données
            if not labels or not valeurs or max(valeurs) == 0:
                parent.add_widget(Label(text=f"Aucune donnée pour {titre}", size_hint_y=None, height=30))
                return
            
            # Créer la figure matplotlib
            fig, ax = plt.subplots(figsize=(8, 4), dpi=80)
            ax.plot(labels, valeurs, 'b-o', linewidth=2, markersize=6)
            ax.set_title(titre, fontsize=12)
            ax.set_xlabel('Période', fontsize=10)
            ax.set_ylabel(unite, fontsize=10)
            ax.grid(True, alpha=0.3)
            plt.xticks(rotation=45)
            fig.tight_layout()
            
            # Sauvegarder dans un buffer
            buf = io.BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight', dpi=80)
            buf.seek(0)
            
            # Créer une texture à partir des données
            core_image = CoreImage(buf, ext='png')
            texture = core_image.texture
            
            # Créer l'image Kivy avec la texture
            img = KivyImage(texture=texture, size_hint_y=None, height=300)
            
            parent.add_widget(Label(text=titre, size_hint_y=None, height=30, bold=True))
            parent.add_widget(img)
            
            plt.close(fig)
            
        except Exception as e:
            print(f"❌ Erreur add_graphique: {e}")
            parent.add_widget(Label(text=f"Erreur graphique: {str(e)[:50]}", size_hint_y=None, height=30))
    
    def add_bar_graphique(self, parent, titre, labels, valeurs, unite):
        """Ajoute un graphique à barres"""
        from kivy.uix.image import Image as KivyImage
        from kivy.core.image import Image as CoreImage
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import io
        
        try:
            # Vérifier qu'il y a des données
            if not labels or not valeurs or max(valeurs) == 0:
                parent.add_widget(Label(text=f"Aucune donnée pour {titre}", size_hint_y=None, height=30))
                return
            
            fig, ax = plt.subplots(figsize=(8, 4), dpi=80)
            ax.bar(labels, valeurs, color='steelblue', alpha=0.7)
            ax.set_title(titre, fontsize=12)
            ax.set_ylabel(unite, fontsize=10)
            plt.xticks(rotation=45)
            fig.tight_layout()
            
            buf = io.BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight', dpi=80)
            buf.seek(0)
            
            core_image = CoreImage(buf, ext='png')
            texture = core_image.texture
            
            img = KivyImage(texture=texture, size_hint_y=None, height=300)
            
            parent.add_widget(Label(text=titre, size_hint_y=None, height=30, bold=True))
            parent.add_widget(img)
            
            plt.close(fig)
            
        except Exception as e:
            print(f"❌ Erreur add_bar_graphique: {e}")

    def add_pie_graphique(self, parent, titre, labels, valeurs):
        """Ajoute un graphique circulaire"""
        from kivy.uix.image import Image as KivyImage
        from kivy.core.image import Image as CoreImage
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import io
        
        try:
            # Filtrer les valeurs nulles
            filtered = [(l, v) for l, v in zip(labels, valeurs) if v and v > 0]
            if not filtered:
                parent.add_widget(Label(text=f"Aucune donnée pour {titre}", size_hint_y=None, height=30))
                return
            
            labels_f, valeurs_f = zip(*filtered)
            
            fig, ax = plt.subplots(figsize=(6, 4), dpi=80)
            ax.pie(valeurs_f, labels=labels_f, autopct='%1.1f%%', startangle=90)
            ax.set_title(titre, fontsize=12)
            ax.axis('equal')
            fig.tight_layout()
            
            buf = io.BytesIO()
            fig.savefig(buf, format='png', bbox_inches='tight', dpi=80)
            buf.seek(0)
            
            core_image = CoreImage(buf, ext='png')
            texture = core_image.texture
            
            img = KivyImage(texture=texture, size_hint_y=None, height=300)
            
            parent.add_widget(Label(text=titre, size_hint_y=None, height=30, bold=True))
            parent.add_widget(img)
            
            plt.close(fig)
            
        except Exception as e:
            print(f"❌ Erreur add_pie_graphique: {e}")
    
    
    def add_kpi_cards(self, parent, kpis):
        """Ajoute des cartes KPI"""
        grid = GridLayout(cols=2, spacing=10, size_hint_y=None, height=200)
        
        for titre, valeur in kpis.items():
            card = RoundedCard(bg_color=(0.2, 0.6, 0.8, 0.2))
            card.add_widget(Label(text=titre, font_size=12, bold=True, halign='center'))
            card.add_widget(Label(text=valeur, font_size=18, bold=True, color=(0.2, 0.8, 0.3, 1), halign='center'))
            grid.add_widget(card)
        
        parent.add_widget(grid)
    
    def on_enter(self):
        """Rafraîchir les données quand on revient sur l'écran"""
        self.load_all_stats()
    
    def go_back(self, instance):
        self.manager.current = 'dashboard'



# ============================================================================
# ÉCRAN ALERTES
# ============================================================================

class AlertesScreen(Screen):
    """Écran des alertes"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='ALERTES', font_size=18, bold=True))
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Liste des alertes
        self.scroll = ScrollView(size_hint=(1, 0.8))
        self.list_layout = BoxLayout(orientation='vertical', spacing=5, padding=10, size_hint_y=None)
        self.list_layout.bind(minimum_height=self.list_layout.setter('height'))
        self.scroll.add_widget(self.list_layout)
        layout.add_widget(self.scroll)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('PRODUITS', 'produits'),
            ('ALERTES', 'alertes')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        
        self.add_widget(layout)
    
    def on_enter(self):
        self.load_alertes()
    
    def load_alertes(self):
        app = App.get_running_app()
        db = app.db
        
        alertes = db.get_alertes_stock()
        
        self.list_layout.clear_widgets()
        
        if not alertes:
            self.list_layout.add_widget(Label(
                text='Aucune alerte',
                size_hint_y=None,
                height=50,
                font_size=16,
                color=(0.5, 0.5, 0.5, 1)
            ))
            return
        
        for a in alertes:
            if a[1] <= 0:
                title = 'RUPTURE'
                color = (1, 0, 0, 1)
                bg_color = (1, 0.8, 0.8, 0.2)
            elif a[1] <= 2:
                title = 'CRITIQUE'
                color = (1, 0.5, 0, 1)
                bg_color = (1, 0.9, 0.8, 0.2)
            else:
                title = 'ALERTE'
                color = (0.8, 0.6, 0, 1)
                bg_color = (1, 1, 0.8, 0.2)
            
            card = RoundedCard(bg_color=bg_color, size_hint_y=None, height=80)
            
            # Titre
            title_label = Label(
                text=f"{a[0][:30]} - {title}",
                font_size=14,
                bold=True,
                color=color
            )
            card.add_widget(title_label)
            
            # Détails
            details_label = Label(
                text=f"Stock: {a[1]} / Seuil: {a[2]} | Prix: {a[3]:,.0f} Fbu",
                font_size=12
            )
            card.add_widget(details_label)
            
            self.list_layout.add_widget(card)
    
    def go_back(self, instance):
        self.manager.current = 'dashboard'


# ============================================================================
# ÉCRAN PARAMETRES_ENTREPRISE
# ============================================================================       
class ParametresScreen(Screen):
    """Écran des paramètres de l'entreprise"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.entreprise_id = None
        self.build_ui()
    
    def build_ui(self):
        """Construit l'interface"""
        layout = BoxLayout(orientation='vertical', padding=10, spacing=10)
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='PARAMÈTRES ENTREPRISE', font_size=18, bold=True))
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Formulaire avec défilement
        scroll = ScrollView(size_hint=(1, 0.8))
        form = BoxLayout(orientation='vertical', padding=10, spacing=8, size_hint_y=None)
        form.bind(minimum_height=form.setter('height'))
        
        # ⭐ Section Informations générales
        form.add_widget(Label(text='INFORMATIONS GÉNÉRALES', font_size=14, bold=True, color=(0.2, 0.6, 0.9, 1)))
        form.add_widget(Widget(size_hint_y=None, height=5))
        
        # Nom de l'entreprise
        form.add_widget(Label(text='Nom de l\'entreprise:', bold=True, size_hint_y=None, height=25))
        self.nom_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.nom_input)
        
        # Slogan
        form.add_widget(Label(text='Slogan:', bold=True, size_hint_y=None, height=25))
        self.slogan_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.slogan_input)
        
        # Adresse
        form.add_widget(Label(text='Adresse:', bold=True, size_hint_y=None, height=25))
        self.adresse_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.adresse_input)
        
        # Téléphone
        form.add_widget(Label(text='Téléphone:', bold=True, size_hint_y=None, height=25))
        self.telephone_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.telephone_input)
        
        # Email
        form.add_widget(Label(text='Email:', bold=True, size_hint_y=None, height=25))
        self.email_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.email_input)
        
        # Site web
        form.add_widget(Label(text='Site web:', bold=True, size_hint_y=None, height=25))
        self.site_web_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.site_web_input)
        
        # Ligne séparatrice
        form.add_widget(Widget(size_hint_y=None, height=10))
        form.add_widget(Label(text='INFORMATIONS LÉGALES', font_size=14, bold=True, color=(0.2, 0.6, 0.9, 1)))
        form.add_widget(Widget(size_hint_y=None, height=5))
        
        # NIF
        form.add_widget(Label(text='NIF (Numéro d\'Identification Fiscale):', bold=True, size_hint_y=None, height=25))
        self.nif_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.nif_input)
        
        # Registre de commerce
        form.add_widget(Label(text='Registre de commerce:', bold=True, size_hint_y=None, height=25))
        self.registre_commerce_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.registre_commerce_input)
        
        # Sécurité sociale
        form.add_widget(Label(text='Sécurité sociale:', bold=True, size_hint_y=None, height=25))
        self.securite_sociale_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.securite_sociale_input)
        
        # Numéro fiscal
        form.add_widget(Label(text='Numéro fiscal:', bold=True, size_hint_y=None, height=25))
        self.numero_fiscal_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.numero_fiscal_input)
        
        # Ligne séparatrice
        form.add_widget(Widget(size_hint_y=None, height=10))
        form.add_widget(Label(text='CONFIGURATION', font_size=14, bold=True, color=(0.2, 0.6, 0.9, 1)))
        form.add_widget(Widget(size_hint_y=None, height=5))
        
        # Devise
        form.add_widget(Label(text='Devise:', bold=True, size_hint_y=None, height=25))
        self.devise_input = TextInput(text='FBu', multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.devise_input)
        
        # TVA par défaut
        form.add_widget(Label(text='TVA par défaut (%):', bold=True, size_hint_y=None, height=25))
        self.tva_defaut_input = TextInput(text='10', multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.tva_defaut_input)
        
        # Langue
        form.add_widget(Label(text='Langue:', bold=True, size_hint_y=None, height=25))
        self.langue_spinner = Spinner(
            text='Français',
            values=['Français', 'English', 'Kiswahili'],
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.langue_spinner)
        
        # Format date
        form.add_widget(Label(text='Format date:', bold=True, size_hint_y=None, height=25))
        self.format_date_spinner = Spinner(
            text='DD/MM/YYYY',
            values=['DD/MM/YYYY', 'MM/DD/YYYY', 'YYYY-MM-DD'],
            size_hint=(1, None),
            height=40
        )
        form.add_widget(self.format_date_spinner)
        
        # Ligne séparatrice
        form.add_widget(Widget(size_hint_y=None, height=10))
        form.add_widget(Label(text='TICKET / IMPRESSION', font_size=14, bold=True, color=(0.2, 0.6, 0.9, 1)))
        form.add_widget(Widget(size_hint_y=None, height=5))
        
        # Entête ticket
        form.add_widget(Label(text='Entête ticket:', bold=True, size_hint_y=None, height=25))
        self.ticket_entete_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.ticket_entete_input)
        
        # Pied ticket
        form.add_widget(Label(text='Pied ticket:', bold=True, size_hint_y=None, height=25))
        self.ticket_pied_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.ticket_pied_input)
        
        # Message fin ticket
        form.add_widget(Label(text='Message de fin:', bold=True, size_hint_y=None, height=25))
        self.ticket_message_fin_input = TextInput(multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.ticket_message_fin_input)
        
        # Ligne séparatrice
        form.add_widget(Widget(size_hint_y=None, height=10))
        form.add_widget(Label(text='ALERTES STOCK', font_size=14, bold=True, color=(0.2, 0.6, 0.9, 1)))
        form.add_widget(Widget(size_hint_y=None, height=5))
        
        # Seuil d'alerte stock
        form.add_widget(Label(text='Seuil d\'alerte stock:', bold=True, size_hint_y=None, height=25))
        self.alerte_stock_input = TextInput(text='5', multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.alerte_stock_input)
        
        # Stock minimum
        form.add_widget(Label(text='Stock minimum:', bold=True, size_hint_y=None, height=25))
        self.stock_minimum_input = TextInput(text='10', multiline=False, size_hint_y=None, height=40)
        form.add_widget(self.stock_minimum_input)
        
        scroll.add_widget(form)
        layout.add_widget(scroll)
        
        # ⭐ Boutons AJOUTER et MODIFIER
        btn_layout = BoxLayout(size_hint_y=None, height=50, spacing=10, padding=10)
        
        self.save_btn = Button(text='AJOUTER', background_color=(0.2, 0.7, 0.3, 1), bold=True)
        self.save_btn.bind(on_press=self.sauvegarder)
        btn_layout.add_widget(self.save_btn)
        
        self.update_btn = Button(text='MODIFIER', background_color=(0.2, 0.5, 0.8, 1), bold=True, disabled=True)
        self.update_btn.bind(on_press=self.modifier)
        btn_layout.add_widget(self.update_btn)
        
        cancel_btn = Button(text='ANNULER', background_color=(0.8, 0.3, 0.3, 1), bold=True)
        cancel_btn.bind(on_press=self.annuler)
        btn_layout.add_widget(cancel_btn)
        
        layout.add_widget(btn_layout)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('NOUVEAU', 'nouvelle_vente'),
            ('PRODUITS', 'produits')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12, bold=True)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        
        self.add_widget(layout)
    
    def on_enter(self):
        """Charge les données quand l'écran s'affiche"""
        self.charger_donnees()
    
    def charger_donnees(self):
        """Charge les paramètres existants"""
        app = App.get_running_app()
        conn = app.db.get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM parametres_entreprise LIMIT 1")
        data = cursor.fetchone()
        conn.close()
        
        if data:
            self.entreprise_id = data[0]
            # Remplir les champs
            self.nom_input.text = data[1] or ''
            self.slogan_input.text = data[13] if len(data) > 13 and data[13] else ''
            self.adresse_input.text = data[2] or ''
            self.telephone_input.text = data[3] or ''
            self.email_input.text = data[4] or ''
            self.site_web_input.text = data[16] if len(data) > 16 and data[16] else ''
            self.nif_input.text = data[25] if len(data) > 25 and data[25] else ''
            self.registre_commerce_input.text = data[26] if len(data) > 26 and data[26] else ''
            self.securite_sociale_input.text = data[27] if len(data) > 27 and data[27] else ''
            self.numero_fiscal_input.text = data[5] if len(data) > 5 and data[5] else ''
            self.devise_input.text = data[6] if len(data) > 6 and data[6] else 'FBu'
            self.tva_defaut_input.text = data[7] if len(data) > 7 and data[7] else '10'
            self.langue_spinner.text = data[8] if len(data) > 8 and data[8] else 'Français'
            self.format_date_spinner.text = data[9] if len(data) > 9 and data[9] else 'DD/MM/YYYY'
            self.ticket_entete_input.text = data[14] if len(data) > 14 and data[14] else ''
            self.ticket_pied_input.text = data[18] if len(data) > 18 and data[18] else ''
            self.ticket_message_fin_input.text = data[19] if len(data) > 19 and data[19] else ''
            self.alerte_stock_input.text = data[10] if len(data) > 10 and data[10] else '5'
            self.stock_minimum_input.text = data[20] if len(data) > 20 and data[20] else '10'
            
            # Changer les boutons
            self.save_btn.disabled = True
            self.save_btn.background_color = (0.5, 0.5, 0.5, 1)
            self.update_btn.disabled = False
            self.update_btn.background_color = (0.2, 0.5, 0.8, 1)
        else:
            # Mode ajout
            self.save_btn.disabled = False
            self.save_btn.background_color = (0.2, 0.7, 0.3, 1)
            self.update_btn.disabled = True
            self.update_btn.background_color = (0.5, 0.5, 0.5, 1)
    
    def sauvegarder(self, instance):
        """Ajoute les paramètres de l'entreprise"""
        try:
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                INSERT INTO parametres_entreprise 
                (nom, adresse, telephone, email, numero_fiscal, devise, tva_defaut, langue, format_date,
                 alerte_stock, stock_minimum, code_auto, slogan, ticket_entete, ticket_pied, 
                 ticket_message_fin, site_web, nif, registre_commerce, securite_sociale)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                self.nom_input.text,
                self.adresse_input.text,
                self.telephone_input.text,
                self.email_input.text,
                self.numero_fiscal_input.text,
                self.devise_input.text,
                self.tva_defaut_input.text,
                self.langue_spinner.text,
                self.format_date_spinner.text,
                self.alerte_stock_input.text,
                self.stock_minimum_input.text,
                'AUTO',  # code_auto par défaut
                self.slogan_input.text,
                self.ticket_entete_input.text,
                self.ticket_pied_input.text,
                self.ticket_message_fin_input.text,
                self.site_web_input.text,
                self.nif_input.text,
                self.registre_commerce_input.text,
                self.securite_sociale_input.text
            ))
            
            conn.commit()
            conn.close()
            
            self.show_message("Succès", "Paramètres ajoutés avec succès")
            self.charger_donnees()  # Recharger pour activer le bouton MODIFIER
            
        except Exception as e:
            self.show_message("Erreur", f"Erreur lors de l'ajout: {str(e)}")
    
    def modifier(self, instance):
        """Modifie les paramètres de l'entreprise"""
        try:
            if not self.entreprise_id:
                self.show_message("Erreur", "Aucun paramètre à modifier")
                return
            
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute('''
                UPDATE parametres_entreprise SET 
                    nom = ?, adresse = ?, telephone = ?, email = ?, numero_fiscal = ?, 
                    devise = ?, tva_defaut = ?, langue = ?, format_date = ?,
                    alerte_stock = ?, stock_minimum = ?, slogan = ?, ticket_entete = ?, 
                    ticket_pied = ?, ticket_message_fin = ?, site_web = ?, 
                    nif = ?, registre_commerce = ?, securite_sociale = ?
                WHERE id = ?
            ''', (
                self.nom_input.text,
                self.adresse_input.text,
                self.telephone_input.text,
                self.email_input.text,
                self.numero_fiscal_input.text,
                self.devise_input.text,
                self.tva_defaut_input.text,
                self.langue_spinner.text,
                self.format_date_spinner.text,
                self.alerte_stock_input.text,
                self.stock_minimum_input.text,
                self.slogan_input.text,
                self.ticket_entete_input.text,
                self.ticket_pied_input.text,
                self.ticket_message_fin_input.text,
                self.site_web_input.text,
                self.nif_input.text,
                self.registre_commerce_input.text,
                self.securite_sociale_input.text,
                self.entreprise_id
            ))
            
            conn.commit()
            conn.close()
            
            self.show_message("Succès", "Paramètres modifiés avec succès")
            
        except Exception as e:
            self.show_message("Erreur", f"Erreur lors de la modification: {str(e)}")
    
    def annuler(self, instance):
        """Annule et retourne au dashboard"""
        self.manager.current = 'dashboard'
    
    def go_back(self, instance):
        """Retour à l'écran précédent"""
        self.manager.current = 'dashboard'
    
    def show_message(self, title, message):
        """Affiche un message popup"""
        content = BoxLayout(orientation='vertical', padding=10, spacing=10)
        content.add_widget(Label(text=message, font_size=14))
        
        btn = Button(text="OK", size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        
        popup.open()
        Clock.schedule_once(lambda dt: popup.dismiss() if popup else None, 3)

# ============================================================================
# ÉCRAN PROFIL
# ============================================================================

class ProfilScreen(Screen):
    """Écran de profil utilisateur"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.user_data = None  # ⭐ Stocker localement
        self.build_ui()
    
    def build_ui(self):
        layout = BoxLayout(orientation='vertical')
        
        # En-tête
        header = BoxLayout(size_hint=(1, 0.1), padding=5)
        back_btn = Button(text='GO BACK', size_hint=(0.15, 1), font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        header.add_widget(Label(text='MON PROFIL', font_size=18, bold=True))
        header.add_widget(Widget(size_hint_x=0.15))
        layout.add_widget(header)
        
        # Conteneur pour le contenu (sera mis à jour dans on_enter)
        self.content_container = BoxLayout(orientation='vertical', size_hint=(1, 0.8))
        layout.add_widget(self.content_container)
        
        # Barre de navigation
        nav = BoxLayout(size_hint=(1, 0.1), spacing=2)
        nav_buttons = [
            ('ACCUEIL', 'dashboard'),
            ('VENTES', 'ventes'),
            ('PRODUITS', 'produits'),
            ('CLIENTS', 'clients')
        ]
        for text, screen in nav_buttons:
            btn = Button(text=text, font_size=12)
            btn.bind(on_press=lambda x, s=screen: setattr(self.manager, 'current', s))
            nav.add_widget(btn)
        
        layout.add_widget(nav)
        self.add_widget(layout)
    
    def on_enter(self):
        """Rafraîchit l'affichage quand on entre dans l'écran"""
        print("🔄 ProfilScreen.on_enter appelé")
        self.refresh_profile()
    
    def refresh_profile(self):
        """Rafraîchit le profil"""
        app = App.get_running_app()
        user_data = app.user_data if hasattr(app, 'user_data') else None
        
        print(f"🔍 [PROFIL] user_data reçu: {user_data}")  # ⭐ LOG IMPORTANT
        
        # Vider le conteneur
        self.content_container.clear_widgets()
        
        # Créer le contenu avec ScrollView
        scroll = ScrollView(size_hint=(1, 1))
        content = BoxLayout(orientation='vertical', padding=20, spacing=15, size_hint_y=None)
        content.bind(minimum_height=content.setter('height'))
        
        if user_data and user_data.get('username'):
            print(f"✅ [PROFIL] Utilisateur trouvé: {user_data.get('username')}")
            
            # Avatar
            avatar = Label(text='', font_size=80, size_hint_y=None, height=120)
            content.add_widget(avatar)
            
            # Nom complet
            full_name = user_data.get('full_name', 'Utilisateur')
            if not full_name:
                full_name = user_data.get('username', 'Utilisateur')
            
            content.add_widget(Label(
                text=full_name,
                font_size=22,
                bold=True,
                size_hint_y=None,
                height=40
            ))
            
            # Rôle
            role = user_data.get('role', 'utilisateur').upper()
            role_color = (0.2, 0.6, 0.9, 1) if role == 'ADMIN' else (0.3, 0.7, 0.4, 1)
            
            content.add_widget(Label(
                text=f"Rôle: {role}",
                font_size=16,
                color=role_color,
                size_hint_y=None,
                height=30
            ))
            
            # Séparateur
            content.add_widget(Widget(size_hint_y=None, height=20))
            
            # Détails
            details_frame = BoxLayout(orientation='vertical', padding=15, spacing=10, size_hint_y=None)
            details_frame.bind(minimum_height=details_frame.setter('height'))
            
            details = [
                f"Nom d'utilisateur: {user_data.get('username', '')}",
                f"Email: {user_data.get('email', 'Non renseigné')}",
                f"ID: {user_data.get('id', 'N/A')}"
            ]
            
            for detail in details:
                detail_label = Label(
                    text=detail,
                    font_size=14,
                    halign='left',
                    size_hint_y=None,
                    height=30
                )
                detail_label.bind(width=lambda s, w: s.setter('text_size')(s, (w, None)))
                details_frame.add_widget(detail_label)
            
            # Permissions
            if user_data.get('permissions'):
                details_frame.add_widget(Widget(size_hint_y=None, height=5))
                permissions = user_data.get('permissions', {})
                modules_actifs = [m for m, perms in permissions.items() if perms]
                
                if modules_actifs:
                    perm_title = Label(
                        text="Modules disponibles:",
                        font_size=14,
                        bold=True,
                        color=(0.8, 0.8, 0.8, 1),
                        size_hint_y=None,
                        height=25,
                        halign='left'
                    )
                    perm_title.bind(width=lambda s, w: s.setter('text_size')(s, (w, None)))
                    details_frame.add_widget(perm_title)
                    
                    modules_text = ', '.join(modules_actifs[:5])
                    if len(modules_actifs) > 5:
                        modules_text += f" et {len(modules_actifs) - 5} autre(s)"
                    
                    perm_label = Label(
                        text=modules_text,
                        font_size=14,
                        color=(0.2, 0.8, 0.2, 1),
                        size_hint_y=None,
                        height=25,
                        halign='left'
                    )
                    perm_label.bind(width=lambda s, w: s.setter('text_size')(s, (w, None)))
                    details_frame.add_widget(perm_label)
            
            content.add_widget(details_frame)
            
            # ⭐⭐⭐ AJOUT DU BOUTON GESTION DES UTILISATEURS (SEULEMENT POUR ADMIN) ⭐⭐⭐
            if user_data.get('role') == 'admin':
                content.add_widget(Widget(size_hint_y=None, height=10))
                
                users_btn = Button(
                    text='GESTION DES UTILISATEURS',
                    size_hint=(1, None),
                    height=50,
                    background_color=(0.2, 0.8, 0.2, 1),
                    font_size=14,
                    bold=True
                )
                users_btn.bind(on_press=self.go_to_users)
                content.add_widget(users_btn)
                print("✅ Bouton GESTION DES UTILISATEURS ajouté dans Profil")
            
            # Espace
            content.add_widget(Widget(size_hint_y=None, height=30))
            
            # Bouton déconnexion
            logout_btn = Button(
                text='SE DÉCONNECTER',
                size_hint=(1, None),
                height=50,
                background_color=(0.8, 0.2, 0.2, 1),
                font_size=16,
                bold=True
            )
            logout_btn.bind(on_press=self.logout)
            content.add_widget(logout_btn)
            
        else:
            print(f"❌ [PROFIL] Aucun utilisateur connecté - user_data: {user_data}")
            content.add_widget(Label(
                text="Aucun utilisateur connecté",
                font_size=18,
                color=(0.8, 0.2, 0.2, 1),
                size_hint_y=None,
                height=100
            ))
            
            reconnect_btn = Button(
                text='SE CONNECTER',
                size_hint=(1, None),
                height=50,
                background_color=(0.2, 0.6, 0.9, 1),
                font_size=16
            )
            reconnect_btn.bind(on_press=lambda x: setattr(self.manager, 'current', 'login'))
            content.add_widget(reconnect_btn)
        
        scroll.add_widget(content)
        self.content_container.add_widget(scroll)
    
    def go_back(self, instance):
        self.manager.current = 'dashboard'
    
    def go_to_users(self, instance):
        """Va à l'écran de gestion des utilisateurs"""
        print("👥 Navigation vers gestion des utilisateurs depuis Profil")
        self.manager.current = 'users'
    
    def logout(self, instance):
        app = App.get_running_app()
        if app.network:
            app.network.disconnect()
            app.network.authenticated = False
            app.network.current_user = None
        app.user_data = {}
        self.manager.current = 'login'
        
        
# ============================================================================
# ECRAN LOGS ACTIVITES
# ============================================================================        
        
class LogsActiviteScreen(Screen):
    """Écran pour visualiser tous les logs d'activité"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.build_ui()
    
    def build_ui(self):
        from kivy.graphics import Color, Rectangle        
        layout = BoxLayout(orientation='vertical', padding=5)
        
        # En-tête
        header = BoxLayout(size_hint_y=None, height=50, padding=5)
        back_btn = Button(text='GO BACK', size_hint_x=0.1, font_size=14, bold=True)
        back_btn.bind(on_press=self.go_back)
        header.add_widget(back_btn)
        
        header.add_widget(Label(text='LOGS D\'ACTIVITÉ', font_size=18, bold=True))
        
        # Boutons d'action
        btn_layout = BoxLayout(size_hint_x=0.3, spacing=5)
        refresh_btn = Button(text='RAFR', font_size=16, size_hint_x=0.5, background_color=(0.2, 0.6, 0.8, 1))
        refresh_btn.bind(on_press=self.refresh_logs)
        btn_layout.add_widget(refresh_btn)
        
        clear_btn = Button(text='DEL', font_size=16, size_hint_x=0.5, background_color=(0.8, 0.2, 0.2, 1))
        clear_btn.bind(on_press=self.clear_logs)
        btn_layout.add_widget(clear_btn)
        
        header.add_widget(btn_layout)
        layout.add_widget(header)
        
        # Filtres
        filter_layout = BoxLayout(size_hint_y=None, height=40, spacing=5, padding=5)

        filter_layout.add_widget(Label(text='Filtrer:', size_hint_x=0.15, font_size=12))

        self.filter_spinner = Spinner(
            text='Tous',
            values=['Tous', 'Connexion', 'Vente', 'Produit', 'Client', 'Paiement', 'Erreur', 'Synchronisation'],
            size_hint_x=0.3,
            height=35
        )
        self.filter_spinner.bind(text=self.on_filter_change)
        filter_layout.add_widget(self.filter_spinner)

        self.search_input = TextInput(
            hint_text='Rechercher...',
            size_hint_x=0.55,
            height=40,
            multiline=False
        )
        self.search_input.bind(text=self.on_search)
        filter_layout.add_widget(self.search_input)

        layout.add_widget(filter_layout)
        
        # Liste des logs avec défilement
        self.scroll = ScrollView(size_hint=(1, 0.85))
        self.logs_container = BoxLayout(orientation='vertical', size_hint_y=None, spacing=2)
        self.logs_container.bind(minimum_height=self.logs_container.setter('height'))
        self.scroll.add_widget(self.logs_container)
        layout.add_widget(self.scroll)
        
        self.add_widget(layout)
    
    def on_enter(self):
        """Charge les logs quand on arrive sur l'écran"""
        self.refresh_logs()
    
    def refresh_logs(self, instance=None):
        """Rafraîchit l'affichage des logs"""
        app = App.get_running_app()
        
        try:
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            # Vérifier si la table existe
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='logs_activite'")
            if not cursor.fetchone():
                self.logs_container.clear_widgets()
                self.logs_container.add_widget(Label(
                    text="Table logs_activite non trouvée. Création en cours...",
                    color=(1, 0.5, 0, 1),
                    size_hint_y=None,
                    height=40
                ))
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS logs_activite (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        utilisateur_nom TEXT,
                        action TEXT,
                        module TEXT,
                        date_action TEXT,
                        details TEXT,
                        last_sync TEXT
                    )
                ''')
                conn.commit()
                conn.close()
                return
            
            # Récupérer les logs
            cursor.execute("""
                SELECT id, utilisateur_nom, action, module, date_action, details
                FROM logs_activite
                ORDER BY date_action DESC
                LIMIT 200
            """)
            
            logs = cursor.fetchall()
            conn.close()
            
            self.display_logs(logs)
            
        except Exception as e:
            print(f"❌ Erreur chargement logs: {e}")
            import traceback
            traceback.print_exc()
            self.logs_container.clear_widgets()
            self.logs_container.add_widget(Label(
                text=f"Erreur: {str(e)}",
                color=(1, 0, 0, 1),
                size_hint_y=None,
                height=40
            ))
    
    def display_logs(self, logs):
        """Affiche les logs avec filtrage"""
        self.logs_container.clear_widgets()
        
        if not logs:
            self.logs_container.add_widget(Label(
                text='Aucun log d\'activité',
                size_hint_y=None,
                height=40,
                color=(0.5, 0.5, 0.5, 1)
            ))
            return
        
        # Appliquer les filtres
        filter_type = self.filter_spinner.text
        search_text = self.search_input.text.lower()
        
        filtered_logs = []
        for log in logs:
            log_id, utilisateur, action, module, date_action, details = log
            
            # Filtre par type
            if filter_type != 'Tous':
                if filter_type == 'Connexion' and action not in ['connexion', 'login', 'logout']:
                    continue
                elif filter_type == 'Vente' and action not in ['vente', 'facture', 'paiement', 'paiement_facture']:
                    continue
                elif filter_type == 'Produit' and action not in ['produit', 'produit_ajout', 'produit_modification', 'produit_stock', 'produit_suppression', 'produit_desactivation']:
                    continue
                elif filter_type == 'Client' and action not in ['client', 'client_ajout', 'client_modification', 'client_suppression', 'client_consultation']:
                    continue
                elif filter_type == 'Paiement' and action not in ['paiement', 'paiement_facture']:
                    continue
                elif filter_type == 'Erreur' and 'erreur' not in action.lower() and 'error' not in action.lower():
                    continue
                elif filter_type == 'Synchronisation' and action not in ['sync', 'synchronisation']:
                    continue
            
            # Filtre par recherche
            if search_text:
                searchable = f"{utilisateur} {action} {module} {details}".lower()
                if search_text not in searchable:
                    continue
            
            filtered_logs.append(log)
        
        if not filtered_logs:
            self.logs_container.add_widget(Label(
                text='Aucun log correspondant aux filtres',
                size_hint_y=None,
                height=40,
                color=(0.5, 0.5, 0.5, 1)
            ))
            return
        
        # Afficher les logs
        for log in filtered_logs:
            log_id, utilisateur, action, module, date_action, details = log
            
            # Formater la date
            if date_action:
                try:
                    date_obj = datetime.fromisoformat(date_action)
                    date_str = date_obj.strftime('%d/%m/%Y %H:%M:%S')
                except:
                    date_str = date_action[:19] if len(date_action) > 19 else date_action
            else:
                date_str = 'N/A'
            
            # Déterminer la couleur selon l'action
            color = (0.8, 0.8, 0.8, 1)  # Gris par défaut
            
            if 'erreur' in action.lower() or 'error' in action.lower():
                color = (1, 0.3, 0.3, 1)  # Rouge
            elif action in ['connexion', 'login']:
                color = (0.3, 0.8, 0.3, 1)  # Vert
            elif action in ['vente', 'facture']:
                color = (0.2, 0.6, 0.9, 1)  # Bleu
            elif action in ['paiement', 'paiement_facture']:
                color = (0.9, 0.6, 0.2, 1)  # Orange
            elif action in ['produit_ajout']:
                color = (0.2, 0.8, 0.4, 1)  # Vert clair
            elif action in ['produit_modification']:
                color = (0.4, 0.6, 0.9, 1)  # Bleu clair
            elif action in ['produit_stock']:
                color = (0.9, 0.5, 0.3, 1)  # Orange
            elif action in ['produit_suppression', 'produit_desactivation']:
                color = (0.9, 0.2, 0.2, 1)  # Rouge foncé
            elif action in ['client_ajout']:
                color = (0.2, 0.8, 0.4, 1)  # Vert clair
            elif action in ['client_modification']:
                color = (0.4, 0.6, 0.9, 1)  # Bleu clair
            elif action in ['client_suppression']:
                color = (0.9, 0.2, 0.2, 1)  # Rouge foncé
            elif action in ['client_consultation']:
                color = (0.5, 0.5, 0.8, 1)  # Violet clair
            
            # Créer le widget du log
            log_frame = BoxLayout(orientation='vertical', size_hint_y=None, padding=5)
            log_frame.height = 60
            
            # Ligne 1: Date et utilisateur
            line1 = BoxLayout(size_hint_y=None, height=25)
            line1.add_widget(Label(text=date_str, font_size=12, color=(0.2, 0.6, 0.9, 1), size_hint_x=0.35, halign='left'))
            line1.add_widget(Label(text=f"{utilisateur}", font_size=12, color=color, size_hint_x=0.3, halign='left'))
            line1.add_widget(Label(text=f"{module}", font_size=12, color=(0.2, 0.8, 0.2, 1), size_hint_x=0.35, halign='right'))
            log_frame.add_widget(line1)
            
            # Ligne 2: Action et détails
            line2 = BoxLayout(size_hint_y=None, height=25)
            
            # Formater l'affichage de l'action
            action_display = action
            if action == 'produit_ajout':
                action_display = 'AJOUT PRODUIT'
            elif action == 'produit_modification':
                action_display = 'MODIF PRODUIT'
            elif action == 'produit_stock':
                action_display = 'STOCK'
            elif action == 'produit_suppression':
                action_display = 'SUPPR PRODUIT'
            elif action == 'produit_desactivation':
                action_display = 'DÉSACT PRODUIT'
            elif action == 'client_ajout':
                action_display = 'AJOUT CLIENT'
            elif action == 'client_modification':
                action_display = 'MODIF CLIENT'
            elif action == 'client_suppression':
                action_display = 'SUPPR CLIENT'
            elif action == 'client_consultation':
                action_display = 'CONSULTATION'
            else:
                action_display = action.upper()
            
            line2.add_widget(Label(text=action_display, font_size=12, bold=True, color=color, size_hint_x=0.4, halign='left'))
            line2.add_widget(Label(text=details[:50] + ('...' if len(details) > 50 else ''), font_size=10, color=(0.7, 0.7, 0.7, 1), halign='left'))
            log_frame.add_widget(line2)
            
            self.logs_container.add_widget(log_frame)
            
    
    def on_filter_change(self, instance, value):
        """Quand le filtre change"""
        self.refresh_logs()
    
    def on_search(self, instance, value):
        """Quand la recherche change"""
        self.refresh_logs()
    
        
    def clear_logs(self, instance):
        """Affiche une confirmation avant de supprimer les logs - Réservé aux administrateurs"""
        
        # ⭐ Vérifier si l'utilisateur est admin
        app = App.get_running_app()
        user_role = app.user_data.get('role') if app.user_data else 'viewer'
        
        if user_role != 'admin':
            self.show_message("Accès refusé", "Seuls les administrateurs peuvent supprimer les logs")
            return
        
        # Compter le nombre total de logs
        conn = app.db.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM logs_activite")
        total_logs = cursor.fetchone()[0]
        conn.close()
        
        content = BoxLayout(orientation='vertical', spacing=10, padding=10)
        
        content.add_widget(Label(text='SUPPRESSION DES LOGS', font_size=14, bold=True))
        content.add_widget(Label(text=f'Nombre total de logs: {total_logs}', font_size=12))
        
        # Choix de la période (sans "Tout supprimer")
        period_layout = BoxLayout(size_hint_y=None, height=40, spacing=10)
        period_layout.add_widget(Label(text='Période à conserver:', size_hint_x=0.5, font_size=12))
        
        period_spinner = Spinner(
            text='1 mois',
            values=['1 semaine', '2 semaines', '1 mois', '3 mois', '6 mois', '1 an'],
            size_hint_x=0.5,
            height=35
        )
        period_layout.add_widget(period_spinner)
        content.add_widget(period_layout)
        
        content.add_widget(Label(
            text='⚠️ Les logs plus anciens que la période sélectionnée seront supprimés.',
            font_size=10,
            color=(1, 0.5, 0, 1)
        ))
        
        btn_layout = BoxLayout(size_hint_y=None, height=40, spacing=5)
        
        def do_clear(instance):
            periode = period_spinner.text
            
            # Déterminer la date limite
            if periode == '1 semaine':
                date_limit = "datetime('now', '-7 days')"
            elif periode == '2 semaines':
                date_limit = "datetime('now', '-14 days')"
            elif periode == '1 mois':
                date_limit = "datetime('now', '-1 month')"
            elif periode == '3 mois':
                date_limit = "datetime('now', '-3 months')"
            elif periode == '6 mois':
                date_limit = "datetime('now', '-6 months')"
            elif periode == '1 an':
                date_limit = "datetime('now', '-1 year')"
            else:
                date_limit = "datetime('now', '-1 month')"  # Par défaut 1 mois
            
            app = App.get_running_app()
            conn = app.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(f"""
                DELETE FROM logs_activite 
                WHERE date_action < {date_limit}
            """)
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            popup.dismiss()
            self.refresh_logs()
            
            if deleted_count > 0:
                self.show_message("Succès", f"{deleted_count} log(s) supprimé(s)")
            else:
                self.show_message("Information", "Aucun log à supprimer")
        
        def do_cancel(instance):
            popup.dismiss()
        
        clear_btn = Button(text='SUPPRIMER', background_color=(0.8, 0.2, 0.2, 1))
        clear_btn.bind(on_press=do_clear)
        btn_layout.add_widget(clear_btn)
        
        cancel_btn = Button(text='ANNULER', background_color=(0.3, 0.3, 0.3, 1))
        cancel_btn.bind(on_press=do_cancel)
        btn_layout.add_widget(cancel_btn)
        
        content.add_widget(btn_layout)
        
        popup = Popup(title='Confirmation - Suppression des logs', content=content, size_hint=(0.8, 0.5))
        popup.open()
    
    def show_message(self, title, message):
        """Affiche un message temporaire"""
        content = BoxLayout(orientation='vertical', padding=10)
        content.add_widget(Label(text=message))
        btn = Button(text='OK', size_hint_y=None, height=40)
        popup = Popup(title=title, content=content, size_hint=(0.7, 0.3))
        btn.bind(on_press=popup.dismiss)
        content.add_widget(btn)
        popup.open()
    
    def go_back(self, instance):
        self.manager.current = 'dashboard'        
        

# ============================================================================
# APPLICATION PRINCIPALE
# ============================================================================

class FacturosMobileApp(App):
    """Application mobile Facturos"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.title = 'Facturos Mobile'
        self.network = MobileNetworkManager(self)
        self.db = MobileDatabase()
        self.invoice_actions = InvoiceActions(self)
        self.user_data = {}  # ⭐ AJOUTER CETTE LIGNE
        self.username = None
        self.password = None
        self.server_host = None
        self.server_port = None
        self.sync_thread_running = False
        self.sync_thread = None
        self.first_sync_done = False
        self.ensure_database_exists()

    def build(self):
        """Construit l'interface"""
        
        # ⭐ CHARGER L'ICÔNE (AJOUTEZ CES LIGNES)
        if os.path.exists('icone.ico'):
            Window.set_icon('icone.ico')
            print("✅ Icône chargée")
        elif os.path.exists('icone.png'):
            Window.set_icon('icone.png')
            print("✅ Icône PNG chargée")
        
        # Le reste de votre code build...
        if platform == 'android' or platform == 'ios':
            Window.fullscreen = True
        else:
            Window.size = (400, 700)
            
        sm = ScreenManager()
        
        sm.add_widget(LoginScreen(name='login'))
        sm.add_widget(DashboardScreen(name='dashboard'))  # ⭐ 'dashboard'
        sm.add_widget(VentesScreen(name='ventes'))
        sm.add_widget(NouvelleVenteAvanceeScreen(name='nouvelle_vente'))
        sm.add_widget(ProduitsScreen(name='produits'))
        sm.add_widget(ClientsScreen(name='clients'))
        sm.add_widget(AlertesScreen(name='alertes'))
        sm.add_widget(ProfilScreen(name='profil'))
        sm.add_widget(ClientDetailScreen(name='client_detail'))
        sm.add_widget(ClientFormScreen(name='client_form'))
        sm.add_widget(ClientHistoryScreen(name='client_history'))
        sm.add_widget(ProductFormScreen(name='product_form'))
        sm.add_widget(StatistiquesAvanceesScreen(name='stats_avancees'))
        sm.add_widget(UsersManagementScreen(name='users'))  # ⭐ 'users'
        sm.add_widget(UserFormScreen(name='user_form'))     # ⭐ 'user_form'
        sm.add_widget(ParametresScreen(name='parametres')) 
        sm.add_widget(LogsActiviteScreen(name='logs_activite'))

        return sm
        
        
    def ensure_database_exists(self):
        """Vérifie que la base existe et la recrée si nécessaire"""
        import os
        db_path = 'facturos_mobile.db'
        
        # Si la base n'existe pas, créer les tables
        if not os.path.exists(db_path):
            print("📁 Base de données manquante - Création...")
            if hasattr(self.db, 'create_tables'):
                self.db.create_tables()
                print("✅ Base recréée avec succès")
            return True
        return False         

    def set_app_icon(self):
        """Définit l'icône de l'application"""
        try:
            # Chercher l'icône dans différents endroits
            icon_path = self.find_icon()
            
            if icon_path and os.path.exists(icon_path):
                Window.set_icon(icon_path)
                print(f"✅ Icône chargée avec succès: {icon_path}")
            else:
                print("ℹ️ Aucune icône personnalisée trouvée, utilisation de l'icône par défaut")
                
        except Exception as e:
            print(f"⚠️ Impossible de charger l'icône: {e}")
    
    def find_icon(self):
        """Recherche l'icône dans les dossiers courants"""
        # Noms possibles pour l'icône
        icon_names = [
            'icone.ico',           # Windows
            'icone.png',           # PNG standard
            'icon.ico',            
            'icon.png',
            'logo.ico',
            'logo.png',
            'facturos.ico',
            'facturos.png',
            'la_sapinette.ico',
            'la_sapinette.png'
        ]
        
        # Dossiers possibles
        folders = [
            '',                     # Racine du projet
            'assets/',
            'images/',
            'icons/',
            'resources/',
            'static/'
        ]
        
        # Parcourir tous les chemins possibles
        for folder in folders:
            for name in icon_names:
                path = os.path.join(folder, name)
                if os.path.exists(path):
                    print(f"🔍 Icône trouvée: {path}")
                    return path
        
        return None
    
    def on_start(self):
        print("🚀 Facturos Mobile démarré")
        self.start_auto_sync()
        
        # ⭐ Forcer une synchronisation immédiate
        if self.network and self.network.connected:
            Clock.schedule_once(lambda dt: self.network.request_sync(), 2)
            
        # ⭐ Ajouter un log de test
        try:
            self.db.add_log(
                'System',
                'demarrage',
                'Application',
                'Application démarrée avec succès'
            )
        except Exception as e:
            print(f"⚠️ Erreur log démarrage: {e}")            
            
    
    def on_stop(self):
        print("🛑 Arrêt de Facturos Mobile")
        self.sync_thread_running = False
        if hasattr(self, 'network'):
            self.network.disconnect()
    
    def start_auto_sync(self):
        """Démarre la synchronisation automatique"""
        self.sync_thread_running = True
        
        def sync_loop():
            print("🔄 Thread de synchronisation démarré")
            last_full_sync = time.time()
            
            while self.sync_thread_running:
                try:
                    if self.network and self.network.connected:
                        self.sync_pending()
                        
                        if time.time() - last_full_sync > 120:
                            print("🔄 Demande de synchronisation complète")
                            self.network.request_sync()
                            last_full_sync = time.time()
                except Exception as e:
                    print(f"❌ Erreur sync loop: {e}")
                
                time.sleep(SYNC_INTERVAL)
        
        self.sync_thread = threading.Thread(target=sync_loop, daemon=True)
        self.sync_thread.start()
        print("🔄 Synchronisation automatique démarrée")
        
    def sync_pending(self):
        """Synchronise les données en attente"""
        if not self.network or not self.network.connected:
            return
        
        try:
            pending = self.db.get_pending_sync()
            
            if pending['factures']:
                print(f"🔄 {len(pending['factures'])} facture(s) en attente")
                
                for f in pending['factures']:
                    # ⭐⭐⭐ CORRECTION: Utiliser les BONS INDEX ⭐⭐⭐
                    # f[0] = id
                    # f[1] = numero
                    # f[2] = client_id
                    # f[3] = date
                    # f[4] = total_ht
                    # f[5] = total_tva
                    # f[6] = total_ttc
                    # f[7] = statut
                    # f[8] = mode_paiement
                    # f[9] = montant_paye
                    # f[10] = reste_a_payer
                    
                    facture_data = {
                        'id': f[0],
                        'numero': f[1],
                        'client_id': f[2],
                        'date': f[3],
                        'total_ht': float(f[4]) if f[4] else 0,
                        'total_tva': float(f[5]) if f[5] else 0,
                        'total_ttc': float(f[6]) if f[6] else 0,
                        'statut': f[7] if f[7] else 'payée',
                        'mode_paiement': f[8] if f[8] else 'Espèces',
                        'montant_paye': float(f[9]) if f[9] else 0,
                        'reste_a_payer': float(f[10]) if f[10] else 0
                    }
                    
                    print(f"\n📤 SYNC PENDING - Envoi facture:")
                    print(f"   numero: {facture_data['numero']}")
                    print(f"   total_ht: {facture_data['total_ht']}")
                    print(f"   total_tva: {facture_data['total_tva']}")
                    print(f"   total_ttc: {facture_data['total_ttc']}")
                    print(f"   statut: {facture_data['statut']}")
                    print(f"   mode_paiement: {facture_data['mode_paiement']}")
                    print(f"   montant_paye: {facture_data['montant_paye']}")
                    print(f"   reste_a_payer: {facture_data['reste_a_payer']}")
                    
                    if self.network.send_update('factures', 'insert', facture_data):
                        self.db.mark_synced('factures', f[0])
                        print(f"✅ Facture {f[1]} synchronisée")
                
                print(f"\n✅ {len(pending['factures'])} factures synchronisées")
            
            if not self.first_sync_done:
                self.network.request_sync()
                self.first_sync_done = True
                    
        except Exception as e:
            print(f"❌ Erreur sync_pending: {e}")
            import traceback
            traceback.print_exc()
            
    def sync_all_local_data(self):
        """Envoie toutes les données locales au serveur"""
        if not self.network or not self.network.connected:
            print("⚠️ Non connecté au serveur - synchronisation impossible")
            return
        
        print("\n" + "="*60)
        print("📤 SYNCHRONISATION DES DONNÉES LOCALES VERS LE SERVEUR")
        print("="*60)
        
        conn = self.db.get_connection()
        cursor = conn.cursor()
        
        # 1. Synchroniser les clients
        try:
            cursor.execute("SELECT * FROM clients")
            clients = cursor.fetchall()
            print(f"📋 Clients à synchroniser: {len(clients)}")
            
            for client in clients:
                client_data = {
                    'id': client[0],
                    'nom': client[1],
                    'email': client[2] if len(client) > 2 else '',
                    'telephone': client[3] if len(client) > 3 else '',
                    'adresse': client[4] if len(client) > 4 else '',
                    'ville': client[5] if len(client) > 5 else '',
                    'pays': client[6] if len(client) > 6 else '',
                    'type_client': client[7] if len(client) > 7 else 'Particulier',
                    'notes': client[8] if len(client) > 8 else '',
                    'uuid': client[11] if len(client) > 11 else str(uuid.uuid4())
                }
                self.network.send_update('clients', 'insert', client_data)
                print(f"   ✅ Client envoyé: {client[1]}")
        except Exception as e:
            print(f"❌ Erreur synchronisation clients: {e}")
        
        # 2. Synchroniser les produits
        try:
            cursor.execute("SELECT * FROM produits")
            produits = cursor.fetchall()
            print(f"📦 Produits à synchroniser: {len(produits)}")
            
            for produit in produits:
                produit_data = {
                    'id': produit[0],
                    'nom': produit[1],
                    'barcode': produit[2] if len(produit) > 2 else '',
                    'description': produit[3] if len(produit) > 3 else '',
                    'prix': produit[4] if len(produit) > 4 else 0,
                    'tva': produit[5] if len(produit) > 5 else 0,
                    'categorie_id': produit[6] if len(produit) > 6 else None,
                    'quantite_stock': produit[7] if len(produit) > 7 else 0,
                    'seuil_alerte': produit[8] if len(produit) > 8 else 5,
                    'uuid': produit[11] if len(produit) > 11 else str(uuid.uuid4())
                }
                self.network.send_update('produits', 'insert', produit_data)
                print(f"   ✅ Produit envoyé: {produit[1]}")
        except Exception as e:
            print(f"❌ Erreur synchronisation produits: {e}")
        
        # 3. Synchroniser les catégories
        try:
            cursor.execute("SELECT * FROM categories")
            categories = cursor.fetchall()
            print(f"📂 Catégories à synchroniser: {len(categories)}")
            
            for cat in categories:
                cat_data = {
                    'id': cat[0],
                    'nom': cat[1],
                    'description': cat[2] if len(cat) > 2 else ''
                }
                self.network.send_update('categories', 'insert', cat_data)
                print(f"   ✅ Catégorie envoyée: {cat[1]}")
        except Exception as e:
            print(f"❌ Erreur synchronisation catégories: {e}")
        
        conn.close()
        print("\n" + "="*60)
        print("✅ SYNCHRONISATION COMPLÈTE TERMINÉE")
        print("="*60)
            
            
    def on_network_connected(self):
        """Appelé lorsque la connexion réseau est établie"""
        print("🌐 Réseau connecté - Synchronisation des données...")
        
        # Synchroniser les factures en attente
        if hasattr(self.db, 'sync_pending_invoices'):
            self.db.sync_pending_invoices()
        
        # Demander une synchronisation complète
        if self.network and self.network.connected:
            # ⭐ Vérifier si la méthode existe
            if hasattr(self.network, 'request_full_sync'):
                self.network.request_full_sync()
            else:
                print("⚠️ request_full_sync non disponible")           
            
    def start_connection_monitor(self):
        """Démarre un thread qui surveille la connexion"""
        def monitor():
            import time
            while self.network and self.network.running:
                time.sleep(30)
                if self.network and self.network.connected:
                    if not self.network.send_ping():
                        print("⚠️ Connexion perdue - tentative de reconnexion...")
                        if self.network.server_host:
                            self.network.connect_to_server(self.network.server_host, self.network.server_port)
        
        monitor_thread = threading.Thread(target=monitor, daemon=True)
        monitor_thread.start()            
    
    def sync_data_received(self, data):
        """Données de synchronisation reçues du serveur"""
        print("📥 Mobile: Données de synchronisation reçues")
        
        try:
            if isinstance(data, dict):
                if 'type' in data and data['type'] == 'sync_data' and 'data' in data:
                    server_data = data['data']
                else:
                    server_data = data
            else:
                print(f"❌ Format de données inattendu: {type(data)}")
                return
            
            # ⭐ DEBUG - Afficher ce qui est reçu
            print(f"🔍 Clés dans server_data: {list(server_data.keys())}")
            
            if 'factures' in server_data:
                print(f"📊 Factures reçues: {len(server_data['factures'])}")
                if server_data['factures']:
                    print(f"   Première facture: {server_data['factures'][0]}")
            
            if 'lignes_facture' in server_data:
                print(f"📊 Lignes facture reçues: {len(server_data['lignes_facture'])}")
            
            def apply_sync():
                if self.db.sync_from_server(server_data):
                    print("✅ Synchronisation réussie !")
                    self.update_all_screens()
                else:
                    print("❌ Échec de la synchronisation")
            
            threading.Thread(target=apply_sync, daemon=True).start()
            
        except Exception as e:
            print(f"❌ Erreur sync_data_received: {e}")
            import traceback
            traceback.print_exc()
    
    def update_all_screens(self):
        """Met à jour tous les écrans - VERSION OPTIMISÉE"""
        try:
            from kivy.clock import Clock
            
            def do_update(dt):
                try:
                    # ⭐ NE PAS RAFRAÎCHIR SI L'ÉCRAN N'EST PAS ACTIF
                    current_screen = self.root.current
                    
                    if self.root.has_screen('dashboard') and current_screen == 'dashboard':
                        self.root.get_screen('dashboard').load_data()
                    
                    if self.root.has_screen('ventes') and current_screen == 'ventes':
                        self.root.get_screen('ventes').load_ventes()
                    
                    # ⭐ POUR PRODUITS, UTILISER refresh_data AU LIEU DE load_products
                    if self.root.has_screen('produits') and current_screen == 'produits':
                        produits_screen = self.root.get_screen('produits')
                        if hasattr(produits_screen, 'refresh_data'):
                            produits_screen.refresh_data()
                        else:
                            produits_screen.load_products()
                    
                    if self.root.has_screen('clients') and current_screen == 'clients':
                        self.root.get_screen('clients').load_clients()
                    
                    if self.root.has_screen('alertes') and current_screen == 'alertes':
                        self.root.get_screen('alertes').load_alertes()
                    
                    print("✅ Écrans mis à jour (seulement l'écran actif)")
                except Exception as e:
                    print(f"❌ Erreur mise à jour: {e}")
            
            Clock.schedule_once(do_update, 0)
            
        except Exception as e:
            print(f"❌ Erreur update_all_screens: {e}")
    
    def apply_server_update(self, data):
        """Applique une mise à jour reçue du serveur"""
        try:
            table = data.get('table')
            action = data.get('action')
            record = data.get('data')
            
            print(f"🔄 Mobile: Application mise à jour {table} - {action}")
            
            # ========== TRAITEMENT DES FACTURES ==========
            if table == 'factures' and action == 'insert':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                numero = record.get('numero')
                if not numero:
                    print("❌ Facture sans numéro, ignorée")
                    conn.close()
                    return
                
                # Vérifier si la facture existe déjà
                cursor.execute("SELECT id FROM factures WHERE numero = ?", (numero,))
                existing = cursor.fetchone()
                
                if not existing:
                    # Récupérer TOUTES les données
                    client_id = record.get('client_id')
                    client_nom = record.get('client_nom', 'Client inconnu')
                    date_fact = record.get('date', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    total_ht = record.get('total_ht', 0)
                    total_tva = record.get('total_tva', 0)
                    total_ttc = record.get('total_ttc', 0)
                    statut = record.get('statut', 'payée')
                    mode_paiement = record.get('mode_paiement', 'Espèces')
                    montant_paye = record.get('montant_paye', 0)
                    reste_a_payer = record.get('reste_a_payer', total_ttc - montant_paye)
                    
                    print(f"📝 Insertion facture {numero}:")
                    print(f"   Client: {client_nom} (ID: {client_id})")
                    print(f"   Date: {date_fact}")
                    print(f"   Total HT: {total_ht:,.0f}")
                    print(f"   Total TVA: {total_tva:,.0f}")
                    print(f"   Total TTC: {total_ttc:,.0f} Fbu")
                    print(f"   Statut: {statut}")
                    print(f"   Mode paiement: {mode_paiement}")
                    print(f"   Montant payé: {montant_paye:,.0f}")
                    print(f"   Reste à payer: {reste_a_payer:,.0f}")
                    
                    # Insérer la facture
                    cursor.execute('''
                        INSERT INTO factures 
                        (numero, client_id, date, total_ht, total_tva, total_ttc, 
                         statut, mode_paiement, montant_paye, reste_a_payer, sync_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        numero,
                        client_id,
                        date_fact,
                        total_ht,
                        total_tva,
                        total_ttc,
                        statut,
                        mode_paiement,
                        montant_paye,
                        reste_a_payer,
                        'synced'
                    ))
                    
                    facture_id = cursor.lastrowid
                    print(f"✅ Mobile: Nouvelle facture {numero} ajoutée (ID: {facture_id})")
                    
                    # Insérer les lignes de facture
                    lignes = record.get('lignes', [])
                    if lignes:
                        print(f"   📦 Insertion de {len(lignes)} lignes...")
                        for ligne in lignes:
                            produit_id = ligne.get('produit_id')
                            quantite = ligne.get('quantite', 1)
                            prix_unitaire = ligne.get('prix_unitaire', 0)
                            taux_tva = ligne.get('taux_tva', 0)
                            montant_tva = ligne.get('montant_tva', 0)
                            total_ligne = ligne.get('total_ligne', prix_unitaire * quantite)
                            nom_produit = ligne.get('nom', f'Produit {produit_id}')
                            
                            cursor.execute('''
                                INSERT INTO lignes_facture 
                                (facture_id, produit_id, quantite, prix_unitaire, 
                                 taux_tva, montant_tva, total_ligne, sync_status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                facture_id,
                                produit_id,
                                quantite,
                                prix_unitaire,
                                taux_tva,
                                montant_tva,
                                total_ligne,
                                'synced'
                            ))
                            print(f"      ✅ {nom_produit}: {quantite} x {prix_unitaire:,.0f} = {total_ligne:,.0f}")
                    
                    conn.commit()
                    
                    # Mettre à jour les stocks
                    for ligne in lignes:
                        produit_id = ligne.get('produit_id')
                        quantite = ligne.get('quantite', 1)
                        
                        cursor.execute("SELECT nom, quantite_stock FROM produits WHERE id = ?", (produit_id,))
                        result = cursor.fetchone()
                        if result:
                            nom_produit, stock_actuel = result
                            nouveau_stock = stock_actuel - quantite
                            cursor.execute("UPDATE produits SET quantite_stock = ? WHERE id = ?", (nouveau_stock, produit_id))
                            print(f"   📦 Stock {nom_produit}: {stock_actuel} → {nouveau_stock}")
                    
                    conn.commit()
                    
                    # Rafraîchir l'écran des ventes
                    self.update_all_screens()
                    print(f"✅ Écrans mis à jour après ajout facture {numero}")
                    
                else:
                    print(f"ℹ️ Mobile: Facture {numero} existe déjà")
                    # Optionnel: mettre à jour la facture existante
                    facture_id = existing[0]
                    cursor.execute('''
                        UPDATE factures SET 
                            client_id = ?, date = ?, total_ht = ?, total_tva = ?, total_ttc = ?,
                            statut = ?, mode_paiement = ?, montant_paye = ?, reste_a_payer = ?
                        WHERE id = ?
                    ''', (
                        record.get('client_id'),
                        record.get('date', datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        record.get('total_ht', 0),
                        record.get('total_tva', 0),
                        record.get('total_ttc', 0),
                        record.get('statut', 'payée'),
                        record.get('mode_paiement', 'Espèces'),
                        record.get('montant_paye', 0),
                        record.get('reste_a_payer', record.get('total_ttc', 0)),
                        facture_id
                    ))
                    conn.commit()
                    print(f"   🔄 Facture {numero} mise à jour")
                
                conn.close()
            
            # ========== TRAITEMENT DES PRODUITS (MISE À JOUR STOCK) ==========
            elif table == 'produits' and action == 'update_stock':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                produit_id = record.get('id') or record.get('produit_id')
                nouveau_stock = record.get('nouveau_stock')
                nom_produit = record.get('nom', f'Produit {produit_id}')
                
                if produit_id and nouveau_stock is not None:
                    cursor.execute("SELECT nom, quantite_stock FROM produits WHERE id = ?", (produit_id,))
                    result = cursor.fetchone()
                    
                    if result:
                        ancien_stock = result[1]
                        cursor.execute('UPDATE produits SET quantite_stock = ? WHERE id = ?', (nouveau_stock, produit_id))
                        conn.commit()
                        print(f"✅ Stock mis à jour: {result[0]} {ancien_stock} → {nouveau_stock}")
                        
                        # Enregistrer le mouvement de stock
                        quantite_vendue = record.get('quantite_vendue', abs(nouveau_stock - ancien_stock))
                        cursor.execute('''
                            INSERT INTO mouvements_stock 
                            (produit_id, type, quantite, date, notes, utilisateur, ancien_stock, nouveau_stock)
                            VALUES (?, 'sortie', ?, datetime('now', 'localtime'), ?, ?, ?, ?)
                        ''', (
                            produit_id,
                            quantite_vendue,
                            f"Mise à jour depuis serveur - {nom_produit}",
                            'Serveur',
                            ancien_stock,
                            nouveau_stock
                        ))
                        conn.commit()
                        
                        self.update_all_screens()
                    else:
                        print(f"⚠️ Produit {produit_id} non trouvé")
                else:
                    print(f"⚠️ Données de stock invalides: produit_id={produit_id}, nouveau_stock={nouveau_stock}")
                
                conn.close()
            
            # ========== TRAITEMENT DES PRODUITS (INSERTION) ==========
            elif table == 'produits' and action == 'insert':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                nom = record.get('nom')
                if not nom:
                    print("❌ Produit sans nom, ignoré")
                    conn.close()
                    return
                
                # Vérifier si le produit existe déjà
                cursor.execute("SELECT id FROM produits WHERE nom = ?", (nom,))
                existing = cursor.fetchone()
                
                if not existing:
                    # Récupérer les données
                    barcode = record.get('barcode', '')
                    categorie = record.get('categorie', 'Non catégorisé')
                    prix = record.get('prix', 0)
                    prix_achat = record.get('prix_achat', 0)
                    tva = record.get('tva', 0)
                    quantite_stock = record.get('quantite_stock', 0)
                    seuil_alerte = record.get('seuil_alerte', 5)
                    description = record.get('description', '')
                    product_uuid = record.get('uuid', str(uuid.uuid4()))
                    
                    cursor.execute('''
                        INSERT INTO produits 
                        (nom, barcode, categorie, prix, prix_achat, tva, 
                         quantite_stock, seuil_alerte, description, uuid, actif)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        nom, barcode, categorie, prix, prix_achat, tva,
                        quantite_stock, seuil_alerte, description, product_uuid, 1
                    ))
                    
                    conn.commit()
                    print(f"✅ Mobile: Nouveau produit {nom} ajouté")
                    self.update_all_screens()
                else:
                    print(f"ℹ️ Mobile: Produit {nom} existe déjà")
                
                conn.close()
            
            # ========== TRAITEMENT DES CLIENTS ==========
            elif table == 'clients' and action == 'insert':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                nom = record.get('nom')
                if not nom:
                    print("❌ Client sans nom, ignoré")
                    conn.close()
                    return
                
                # Vérifier si le client existe déjà
                cursor.execute("SELECT id FROM clients WHERE nom = ?", (nom,))
                existing = cursor.fetchone()
                
                if not existing:
                    # Récupérer les données
                    email = record.get('email', '')
                    telephone = record.get('telephone', '')
                    adresse = record.get('adresse', '')
                    ville = record.get('ville', '')
                    pays = record.get('pays', '')
                    client_uuid = record.get('uuid', str(uuid.uuid4()))
                    
                    cursor.execute('''
                        INSERT INTO clients 
                        (nom, email, telephone, adresse, ville, pays, uuid, statut)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        nom, email, telephone, adresse, ville, pays, client_uuid, 'actif'
                    ))
                    
                    conn.commit()
                    print(f"✅ Mobile: Nouveau client {nom} ajouté")
                    self.update_all_screens()
                else:
                    print(f"ℹ️ Mobile: Client {nom} existe déjà")
                
                conn.close()
            
            # ========== TRAITEMENT DES UTILISATEURS ==========
            elif table == 'users' and action == 'insert':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                username = record.get('username')
                if not username:
                    print("❌ Utilisateur sans nom, ignoré")
                    conn.close()
                    return
                
                # Vérifier si l'utilisateur existe déjà
                cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
                existing = cursor.fetchone()
                
                if not existing:
                    # Récupérer les données
                    password = record.get('password', '')
                    full_name = record.get('full_name', '')
                    email = record.get('email', '')
                    role = record.get('role', 'viewer')
                    is_active = record.get('is_active', 1)
                    permissions = record.get('permissions', '{}')
                    user_uuid = record.get('uuid', str(uuid.uuid4()))
                    
                    cursor.execute('''
                        INSERT INTO users 
                        (username, password, full_name, email, role, is_active, permissions, uuid)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        username, password, full_name, email, role, is_active, permissions, user_uuid
                    ))
                    
                    conn.commit()
                    print(f"✅ Mobile: Nouvel utilisateur {username} ajouté")
                    self.update_all_screens()
                else:
                    print(f"ℹ️ Mobile: Utilisateur {username} existe déjà")
                
                conn.close()
            
            # ========== TRAITEMENT DES MISES À JOUR DE CLIENTS ==========
            elif table == 'clients' and action == 'update':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                client_id = record.get('id')
                nom = record.get('nom')
                
                if client_id and nom:
                    cursor.execute('''
                        UPDATE clients SET 
                            nom = ?, email = ?, telephone = ?, adresse = ?, ville = ?, pays = ?
                        WHERE id = ?
                    ''', (
                        nom,
                        record.get('email', ''),
                        record.get('telephone', ''),
                        record.get('adresse', ''),
                        record.get('ville', ''),
                        record.get('pays', ''),
                        client_id
                    ))
                    conn.commit()
                    print(f"✅ Mobile: Client {nom} mis à jour")
                    self.update_all_screens()
                else:
                    print(f"⚠️ Données client invalides pour mise à jour")
                
                conn.close()
            
            # ========== TRAITEMENT DES MISES À JOUR DE PRODUITS ==========
            elif table == 'produits' and action == 'update':
                conn = self.db.get_connection()
                cursor = conn.cursor()
                
                produit_id = record.get('id')
                nom = record.get('nom')
                
                if produit_id and nom:
                    cursor.execute('''
                        UPDATE produits SET 
                            nom = ?, prix = ?, tva = ?, quantite_stock = ?, seuil_alerte = ?, description = ?
                        WHERE id = ?
                    ''', (
                        nom,
                        record.get('prix', 0),
                        record.get('tva', 0),
                        record.get('quantite_stock', 0),
                        record.get('seuil_alerte', 5),
                        record.get('description', ''),
                        produit_id
                    ))
                    conn.commit()
                    print(f"✅ Mobile: Produit {nom} mis à jour")
                    self.update_all_screens()
                else:
                    print(f"⚠️ Données produit invalides pour mise à jour")
                
                conn.close()
            
            # ========== TRAITEMENT PAR DÉFAUT ==========
            else:
                print(f"⚠️ Mobile: Action non gérée - {table} - {action}")
                
        except Exception as e:
            print(f"❌ Erreur apply_server_update: {e}")
            import traceback
            traceback.print_exc()

    def send_facture(self, facture_data):
        """Envoie une facture au serveur"""
        
        print("="*60)
        print("🔍 send_facture - ENVOI FACTURE AU SERVEUR")
        print("="*60)
        print(f"Numéro: {facture_data.get('numero')}")
        print(f"Total TTC: {facture_data.get('total_ttc')}")
        print(f"Clés dans facture_data: {list(facture_data.keys())}")
        
        # ⭐⭐⭐ LOG DES LIGNES ⭐⭐⭐
        lignes = facture_data.get('lignes', [])
        print(f"Lignes trouvées: {len(lignes)}")
        if lignes:
            for i, ligne in enumerate(lignes):
                print(f"   Ligne {i+1}: {ligne}")
        else:
            print("⚠️ ATTENTION: AUCUNE LIGNE DANS facture_data!")
            # Vérifier si les lignes sont sous un autre nom
            for key in ['articles', 'items', 'produits', 'cart', 'panier']:
                if key in facture_data:
                    print(f"   Alternative trouvée: '{key}' = {facture_data[key]}")
                    lignes = facture_data[key]
                    break
        
        # Construire le message pour le serveur
        message = {
            'type': 'server_update',
            'table': 'factures',
            'action': 'insert',
            'data': facture_data
        }
        
        # Envoyer via le gestionnaire réseau
        if hasattr(self, 'network_manager') and self.network_manager:
            self.network_manager.send_update(message)
            print(f"✅ Facture {facture_data.get('numero')} envoyée au serveur")
        else:
            print(f"❌ Network manager non disponible")


        
    def apply_stock_update(self, data):
        """Applique une mise à jour de stock"""
        try:
            if 'data' in data:
                stock_data = data['data']
            else:
                stock_data = data
            
            produit_id = stock_data.get('id') or stock_data.get('produit_id')
            nouveau_stock = stock_data.get('nouveau_stock')
            
            if not produit_id or nouveau_stock is None:
                return
            
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT nom FROM produits WHERE id = ?", (produit_id,))
            result = cursor.fetchone()
            
            if result:
                cursor.execute('UPDATE produits SET quantite_stock = ? WHERE id = ?', (nouveau_stock, produit_id))
                conn.commit()
                print(f"✅ Stock mis à jour: {result[0]} → {nouveau_stock}")
                
                self.update_all_screens()
            
            conn.close()
            
        except Exception as e:
            print(f"❌ Erreur apply_stock_update: {e}")
            
            
            


# ============================================================================
# POINT D'ENTRÉE
# ============================================================================

if __name__ == '__main__':
    FacturosMobileApp().run()
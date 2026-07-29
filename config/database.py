"""
database.py - Conexión a base de datos SQLite
Aquí manejamos la persistencia de datos
"""

import sqlite3
import os
from config.config import Config

class Database:
    """Gestor de base de datos"""
    
    def __init__(self):
        """Inicializa conexión a BD"""
        self.db_file = Config.DATABASE_FILE
        self.crear_carpeta_si_no_existe()
        self.conectar()
    
    def crear_carpeta_si_no_existe(self):
        """Crea carpeta database/ si no existe"""
        carpeta = os.path.dirname(self.db_file)
        if not os.path.exists(carpeta):
            os.makedirs(carpeta)
            print(f"✅ Carpeta {carpeta} creada")
    
    def conectar(self):
        """Conecta a la base de datos"""
        self.conexion = sqlite3.connect(self.db_file)
        self.cursor = self.conexion.cursor()
        print(f"✅ Conectado a {self.db_file}")
    
    def crear_tablas(self):
        """Crea las tablas si no existen"""
        
        # Tabla de usuarios
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY,
                user_id TEXT UNIQUE,
                username TEXT,
                plan TEXT DEFAULT 'FREE',
                fecha_registro TEXT
            )
        ''')
        
        # Tabla de pagos
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS pagos (
                id INTEGER PRIMARY KEY,
                user_id TEXT,
                monto REAL,
                metodo TEXT,
                estado TEXT,
                fecha TEXT
            )
        ''')
        
        # Tabla de señales
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS senales (
                id INTEGER PRIMARY KEY,
                activo TEXT,
                direccion TEXT,
                confianza REAL,
                rsi REAL,
                precio REAL,
                resultado TEXT,
                fecha TEXT,
                metodo TEXT,
                sesion TEXT
            )
        ''')
        
        self.conexion.commit()
        print("✅ Tablas creadas")
    
    def cerrar(self):
        """Cierra conexión"""
        self.conexion.close()
        print("✅ Conexión cerrada")


# Uso global
db = Database()
"""
revision_semanal.py - Analisis semanal de senales desde la BD
Ejecuta: python revision_semanal.py
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sqlite3
from datetime import datetime, timedelta
from config.config import Config


def conexion():
    conn = sqlite3.connect(Config.DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def resumen_total():
    conn = conexion()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) as total, SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins, SUM(CASE WHEN resultado='LOSS' THEN 1 ELSE 0 END) as losses FROM senales WHERE resultado IN ('WIN','LOSS')")
    r = c.fetchone()
    total = r["total"] or 0
    wins = r["wins"] or 0
    losses = r["losses"] or 0
    efectividad = round(wins / total * 100, 1) if total > 0 else 0
    print(f"\n{'='*60}")
    print(f"  RESUMEN TOTAL - {total} senales evaluadas")
    print(f"{'='*60}")
    print(f"  WIN: {wins}  |  LOSS: {losses}  |  Efectividad: {efectividad}%")
    print(f"{'='*60}")
    conn.close()


def por_sesion():
    conn = conexion()
    c = conn.cursor()
    c.execute("""
        SELECT sesion, COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN resultado='LOSS' THEN 1 ELSE 0 END) as losses
        FROM senales WHERE resultado IN ('WIN','LOSS') AND sesion IS NOT NULL
        GROUP BY sesion ORDER BY total DESC
    """)
    rows = c.fetchall()
    print(f"\n{'='*60}")
    print(f"  POR SESION")
    print(f"{'='*60}")
    if not rows:
        print("  Sin datos de sesion (columna sesion vacia)")
    for r in rows:
        efectividad = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0
        print(f"  {r['sesion']:<22} {r['total']:>3} senales  WIN:{r['wins']}  LOSS:{r['losses']}  -> {efectividad}%")
    print(f"{'='*60}")
    conn.close()


def por_activo():
    conn = conexion()
    c = conn.cursor()
    c.execute("""
        SELECT activo, COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN resultado='LOSS' THEN 1 ELSE 0 END) as losses
        FROM senales WHERE resultado IN ('WIN','LOSS')
        GROUP BY activo ORDER BY total DESC
    """)
    rows = c.fetchall()
    print(f"\n{'='*60}")
    print(f"  POR ACTIVO")
    print(f"{'='*60}")
    for r in rows:
        efectividad = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0
        indicador = "OK" if efectividad >= 60 else ("--" if efectividad >= 50 else "XX")
        print(f"  [{indicador}] {r['activo']:<12} {r['total']:>3} senales  WIN:{r['wins']}  LOSS:{r['losses']}  -> {efectividad}%")
    print(f"{'='*60}")
    conn.close()


def por_metodo():
    conn = conexion()
    c = conn.cursor()
    c.execute("""
        SELECT metodo, COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN resultado='LOSS' THEN 1 ELSE 0 END) as losses
        FROM senales WHERE resultado IN ('WIN','LOSS') AND metodo IS NOT NULL
        GROUP BY metodo ORDER BY total DESC
    """)
    rows = c.fetchall()
    print(f"\n{'='*60}")
    print(f"  POR ESTRATEGIA (METODO)")
    print(f"{'='*60}")
    if not rows:
        print("  Sin datos de metodo (columna metodo vacia)")
    for r in rows:
        efectividad = round(r["wins"] / r["total"] * 100, 1) if r["total"] > 0 else 0
        print(f"  {r['metodo']:<35} {r['total']:>3} senales  WIN:{r['wins']}  LOSS:{r['losses']}  -> {efectividad}%")
    print(f"{'='*60}")
    conn.close()


def ultima_semana():
    conn = conexion()
    c = conn.cursor()
    desde = (datetime.now() - timedelta(days=7)).isoformat()
    c.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN resultado='LOSS' THEN 1 ELSE 0 END) as losses
        FROM senales WHERE resultado IN ('WIN','LOSS') AND fecha >= ?
    """, (desde,))
    r = c.fetchone()
    total = r["total"] or 0
    wins = r["wins"] or 0
    losses = r["losses"] or 0
    efectividad = round(wins / total * 100, 1) if total > 0 else 0
    print(f"\n{'='*60}")
    print(f"  ULTIMOS 7 DIAS (desde {desde[:10]})")
    print(f"{'='*60}")
    print(f"  Total: {total} senales  |  WIN: {wins}  |  LOSS: {losses}  |  Efectividad: {efectividad}%")
    print(f"{'='*60}")
    conn.close()


def top_bottom():
    conn = conexion()
    c = conn.cursor()
    c.execute("""
        SELECT activo, COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins
        FROM senales WHERE resultado IN ('WIN','LOSS')
        GROUP BY activo HAVING total >= 5
        ORDER BY CAST(wins AS FLOAT)/total DESC
        LIMIT 5
    """)
    top = c.fetchall()
    c.execute("""
        SELECT activo, COUNT(*) as total,
               SUM(CASE WHEN resultado='WIN' THEN 1 ELSE 0 END) as wins
        FROM senales WHERE resultado IN ('WIN','LOSS')
        GROUP BY activo HAVING total >= 5
        ORDER BY CAST(wins AS FLOAT)/total ASC
        LIMIT 5
    """)
    bottom = c.fetchall()
    print(f"\n{'='*60}")
    print(f"  TOP 5 MEJORES ACTIVOS (min 5 senales)")
    print(f"{'='*60}")
    for r in top:
        efectividad = round(r["wins"] / r["total"] * 100, 1)
        print(f"  [OK] {r['activo']:<12} {efectividad}% ({r['wins']}/{r['total']})")
    print(f"\n{'='*60}")
    print(f"  BOTTOM 5 PEORES ACTIVOS (min 5 senales)")
    print(f"{'='*60}")
    for r in bottom:
        efectividad = round(r["wins"] / r["total"] * 100, 1)
        print(f"  [XX] {r['activo']:<12} {efectividad}% ({r['wins']}/{r['total']})")
    print(f"{'='*60}")
    conn.close()


if __name__ == "__main__":
    print("\n  REVISION SEMANAL - L&W Senales IA")
    resumen_total()
    ultima_semana()
    por_sesion()
    por_activo()
    por_metodo()
    top_bottom()
    print("\n  Analisis completo. Revisa los datos para ajustar umbrales.\n")

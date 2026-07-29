"""
corregir_resultado.py - Corregir resultado de una senal desde Quotex
Ejecuta: python corregir_resultado.py

Muestra las ultimas senales pendientes o con resultado y permite
corregir el resultado al que aparece en Quotex real.
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sqlite3
from config.config import Config


def conexion():
    conn = sqlite3.connect(Config.DATABASE_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ver_senales_pendientes():
    conn = conexion()
    c = conn.cursor()
    c.execute("""
        SELECT id, activo, direccion, confianza, precio, resultado, fecha, sesion
        FROM senales
        ORDER BY id DESC
        LIMIT 20
    """)
    rows = c.fetchall()
    conn.close()
    print("\n  ULTIMAS 20 SENALES:")
    print(f"  {'ID':<5} {'Activo':<12} {'Direccion':<6} {'Conf':<6} {'Precio':<10} {'Resultado':<10} {'Sesion':<20} {'Fecha'}")
    print("  " + "-" * 110)
    for r in rows:
        resultado = r["resultado"] or "PENDIENTE"
        fecha = r["fecha"][:16] if r["fecha"] else "N/A"
        sesion = r["sesion"] or "N/A"
        print(f"  {r['id']:<5} {r['activo']:<12} {r['direccion']:<6} {r['confianza']:<6} {r['precio']:<10} {resultado:<10} {sesion:<20} {fecha}")
    return rows


def corregir(id_senal, nuevo_resultado):
    conn = conexion()
    c = conn.cursor()
    c.execute("SELECT id, activo, direccion, resultado FROM senales WHERE id = ?", (id_senal,))
    actual = c.fetchone()
    if not actual:
        print(f"  Senal ID {id_senal} no encontrada.")
        conn.close()
        return

    anterior = actual["resultado"] or "PENDIENTE"
    c.execute("UPDATE senales SET resultado = ? WHERE id = ?", (nuevo_resultado, id_senal))
    conn.commit()
    conn.close()
    print(f"  Senal #{id_senal} ({actual['activo']} {actual['direccion']}): {anterior} -> {nuevo_resultado}")


if __name__ == "__main__":
    print("\n  CORRECCION DE RESULTADOS - L&W Senales IA")
    print("  Compara con Quotex y corrige lo que sea necesario.\n")

    ver_senales_pendientes()

    print("\n  Para corregir escribe: CORREGIR <ID> <WIN|LOSS>")
    print("  Ejemplo: CORREGIR 15 WIN")
    print("  Para salir escribe: SALIR\n")

    while True:
        try:
            entrada = input("  > ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            break

        if entrada == "SALIR" or entrada == "":
            break

        partes = entrada.split()
        if len(partes) == 3 and partes[0] == "CORREGIR":
            try:
                id_senal = int(partes[1])
                nuevo = partes[2]
                if nuevo not in ("WIN", "LOSS"):
                    print("  Resultado debe ser WIN o LOSS")
                    continue
                corregir(id_senal, nuevo)
            except ValueError:
                print("  ID debe ser un numero")
        elif entrada == "VER":
            ver_senales_pendientes()
        else:
            print("  Comando no valido. Usa: CORREGIR <ID> <WIN|LOSS>  o  VER  o  SALIR")

    print("  Saliendo.")

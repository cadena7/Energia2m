# boot.py
# Configuración de red para Ethernet EOCZ AGOSTO 2025
import network
import time
import machine
from machine import Pin

ETHERNET_TIMEOUT_MS = 30000

print('Configurando Ethernet...')
lan = network.LAN(mdc=Pin(23), mdio=Pin(18), power=Pin(5), id=None, phy_addr=0,
                  ref_clk=Pin(17), ref_clk_mode=Pin.OUT, phy_type=network.PHY_LAN8710)
lan.active(True)

# set fixed IP (address, netmask, gateway, dns)
lan.ifconfig(('192.168.0.90', '255.255.255.0', '192.168.0.254', '8.8.8.8'))

time.sleep(1)
lan.active(True)
start_ms = time.ticks_ms()
while not lan.isconnected():
    print('Esperando conexión Ethernet...')
    time.sleep(1)
    if time.ticks_diff(time.ticks_ms(), start_ms) > ETHERNET_TIMEOUT_MS:
        print('ERROR: Ethernet no conectó en {} ms. Reiniciando...'.format(
            ETHERNET_TIMEOUT_MS))
        machine.reset()

print('Ethernet configurado. IP:', lan.ifconfig())


"""

# boot.py
# Configuración de red para WiFi
import network
import time
from machine import Pin

print('Configurando WiFi...')

# Desactiva la interfaz de red LAN si está activa
lan = network.LAN(mdc=Pin(23), mdio=Pin(18), power=Pin(5), id=None, phy_addr=0,
                  ref_clk=Pin(17), ref_clk_mode=Pin.OUT, phy_type=network.PHY_LAN8710)
lan.active(False)

# Configura la interfaz de red WiFi
wlan = network.WLAN(network.STA_IF)
wlan.active(True)

# set fixed IP (address, netmask, gateway, dns)
wlan.ifconfig(('192.168.0.208', '255.255.255.0', '192.168.0.254', '8.8.8.8'))

# Conecta a la red WiFi
wlan.connect('LAB01','01239876')

# Espera a que la conexión se establezca
while not wlan.isconnected():
    print('Esperando conexión WiFi...')
    time.sleep(1)

print('WiFi configurado. IP:', wlan.ifconfig())
"""

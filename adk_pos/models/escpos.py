# -*- coding: utf-8 -*-
"""Générateur de commandes ESC/POS (standard de fait des imprimantes de tickets : Epson, Star en
mode émulation, Xprinter, Sunmi, Bixolon, Rongta, HOIN, Gprinter, Munbyn, etc.).

Aucune dépendance externe : on produit des octets bruts, envoyés ensuite par le réseau (port 9100),
l'USB (WebUSB), le port série / Bluetooth (Web Serial) ou une passerelle locale.
"""
import unicodedata

# nom du codec Python -> numéro de page de code ESC/POS (commande ESC t n)
CODEPAGES = {
    'cp858': 19,    # Latin-1 + € : le meilleur choix pour le français
    'cp437': 0,
    'cp850': 2,
    'cp1252': 16,
    'cp860': 3,
    'ascii': None,  # aucune page de code : accents supprimés (compatibilité maximale)
}

ESC = b'\x1b'
GS = b'\x1d'


class EscPos:
    def __init__(self, width=48, codec='cp858'):
        self.width = width            # nombre de caractères par ligne (32 = 58 mm, 48 = 80 mm)
        self.codec = codec if codec in CODEPAGES else 'cp858'
        self.buf = bytearray()
        self.buf += ESC + b'@'        # initialisation
        if CODEPAGES[self.codec] is not None:
            self.buf += ESC + b't' + bytes([CODEPAGES[self.codec]])

    # ---------- texte ----------
    def _enc(self, text):
        text = str(text if text is not None else '')
        if self.codec == 'ascii':
            text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
            return text.encode('ascii', 'replace')
        # NBSP / espaces fines (séparateurs de milliers français) -> espace simple
        for ch in (' ', ' ', ' '):
            text = text.replace(ch, ' ')
        return text.encode(self.codec, 'replace')

    def text(self, text='', newline=True):
        self.buf += self._enc(text) + (b'\n' if newline else b'')
        return self

    def align(self, where='left'):
        self.buf += ESC + b'a' + bytes([{'left': 0, 'center': 1, 'right': 2}[where]])
        return self

    def bold(self, on=True):
        self.buf += ESC + b'E' + bytes([1 if on else 0])
        return self

    def size(self, w=1, h=1):
        self.buf += GS + b'!' + bytes([((w - 1) & 7) << 4 | ((h - 1) & 7)])
        return self

    def line(self, char='-'):
        return self.text(char * self.width)

    def cols(self, left, right):
        """Texte à gauche, valeur alignée à droite, sur toute la largeur."""
        left, right = str(left), str(right)
        room = self.width - len(right) - 1
        if len(left) > room:
            left = left[:max(room, 1)]
        return self.text(left + ' ' * (self.width - len(left) - len(right)) + right)

    def feed(self, n=1):
        self.buf += ESC + b'd' + bytes([n])
        return self

    def cut(self):
        self.buf += b'\n\n\n' + GS + b'V' + b'\x42' + b'\x00'   # avance + coupe partielle
        return self

    def drawer(self):
        """Impulsion d'ouverture du tiroir-caisse branché sur l'imprimante (broche 2 puis 5)."""
        self.buf += ESC + b'p' + b'\x00\x19\xfa' + ESC + b'p' + b'\x01\x19\xfa'
        return self

    def beep(self):
        self.buf += ESC + b'B' + b'\x02\x02'
        return self

    def qr(self, data, size=6):
        data = str(data).encode('utf-8')
        n = len(data) + 3
        self.buf += GS + b'(k\x04\x00\x31\x41\x32\x00'                         # modèle 2
        self.buf += GS + b'(k\x03\x00\x31\x43' + bytes([size])                 # taille
        self.buf += GS + b'(k\x03\x00\x31\x45\x31'                             # correction L/M
        self.buf += GS + b'(k' + bytes([n & 255, n >> 8]) + b'\x31\x50\x30' + data
        self.buf += GS + b'(k\x03\x00\x31\x51\x30'
        return self

    def bytes(self):
        return bytes(self.buf)

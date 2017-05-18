/trade
    sell
        XXBT
            1680
        XETH
        ...
    buy
        XXBT
        XETH
        ...

Antwort Gutfall
    DONE!

Antwort Schlechtfall
    ERROR: ...

Falls Orders existieren
    periodisch checken ob gekauft / verkauft
    falls order prozessiert
        selbstständig nachricht schicken
            XXBT sold for 1600

Wie / wo Datum anhängen an Nachrichten?
Bestätigung von order anfordern?
    fragen ob OK
        wenn ja
            mit "y" antworten
        wenn nein
            egal was antworten (außer "y")
            (bei zu langem warten auf antwort order verschmeißen mit nachricht "order dismissed")
    evtl. nur fragen ob OK wenn Preis von Kauf / Verkauf um bestimmten Faktor zum aktuellen Preis steht?
        evlt. zusätzlich zum fragen ob OK auch diesen Check hinzufügen
    evtl. Passwort anfordern
        Hash in config.json speichern und checken
User-Check über Telnummer machen auch möglich? Option in die config
Passworteingabe entweder global machen, also einmal Login und dann darf ich alles machen bis ich wieder einen Logout mache oder aber bei jeder Transaktion Passwort verlangen? Option in die config

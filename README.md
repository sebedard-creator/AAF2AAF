# AAF2AAF : Pro Tools Fade Un-Renderer

## Description

**AAF2AAF** est un outil en ligne de commande (CLI) autonome écrit en Python. 
Il résout un problème majeur avec les exports AAF de Pro Tools : la destruction des fondus virtuels.

Lorsque Pro Tools exporte un AAF, il consolide les fondus et les rend sous forme de vrais fichiers audio (ex: "Fade 1", "Fade 2"). Cela empêche toute édition ultérieure (trimming, ajustement de la courbe) dans un autre logiciel ou lors d'une réimportation.

**AAF2AAF agit comme un "Un-Renderer" temporel :**
Il parcourt la timeline d'un AAF exporté par Pro Tools, détecte les fichiers audio rendus qui correspondent à des fondus, calcule les *handles* (poignées) des clips audio adjacents, et remplace mathématiquement ces rendus par de véritables **Transitions AAF standard (Audio Dissolve)**.

Le résultat est un nouvel AAF pur (de style Media Composer) qui force Pro Tools (et les autres DAW) à re-générer de vrais fondus virtuels éditables et non-destructifs à l'importation.

## Prérequis

- Python 3.x
- Librairie `pyaaf2` (`pip install pyaaf2`)

## Utilisation

Le script s'utilise directement en ligne de commande, sans interface graphique.

```bash
python AAF2AAF.py <input.aaf> <output.aaf>
```

**Exemple :**
```bash
python AAF2AAF.py mon_export_protools.aaf session_editable.aaf
```

## Fonctionnalités

- **Détection heuristique** : Repère automatiquement les clips nommés "Fade..."
- **Restitution parfaite des Crossfades** : Étire les clips adjacents pour recréer le croisement.
- **Restauration des Fade-In / Fade-Out** : Manipule les "Fillers" (silences) pour recréer les fondus d'ouverture et de fermeture.
- **Respect absolu des Handles** : Ne génère jamais de fondus virtuels plus longs que l'audio réellement disponible dans les fichiers sources consolidés (prévient les erreurs d'import "out of bounds").
- **Correction des désynchronisations** : Maintient l'intégrité absolue de la durée de la timeline, même en cas de fondus tronqués par manque de handles.

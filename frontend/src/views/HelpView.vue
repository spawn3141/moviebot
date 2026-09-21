<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const status = ref(null)

const SORTS = [
  ['Beliebt', 'Beliebtheitswert von TMDB: wie oft ein Titel dort gerade aufgerufen, bewertet und ' +
    'gemerkt wird, und wie nah sein Erscheinungstermin liegt. Weltweit, täglich neu, nicht auf dich ' +
    'zugeschnitten. Misst Aufmerksamkeit, nicht Qualität – Neues und Beworbenes steht vorn.'],
  ['Beste Bewertung', 'Sterne der TMDB-Nutzer, gewichtet nach Anzahl der Stimmen. Dadurch landet ' +
    '9,5 bei 3 Stimmen hinter 7,8 bei 5.000 Stimmen. Deine eigenen Bewertungen spielen hier ' +
    'noch keine Rolle.'],
  ['Neueste zuerst', 'Bei Filmen das Erscheinungsdatum. Bei Serien zählt voreingestellt der ' +
    'Start der neuesten Staffel, sodass eine lang laufende Serie mit frischer Staffel weit oben ' +
    'steht. In den Einstellungen kannst du stattdessen den Start der Serie wählen. Sortiert wird ' +
    'taggenau, angezeigt wird nur das Jahr – das volle Datum steht in der Detailansicht.'],
  ['Zuletzt dazugekommen', 'Der Tag, an dem wir den Titel im jeweiligen Dienst entdeckt haben ' +
    'oder eine neue Staffel bemerkt haben – je nachdem, was neuer ist. Titel aus dem allerersten ' +
    'Abgleich zählen als gleich alt; bei gleichem Tag entscheidet die Beliebtheit.'],
  ['Titel A–Z', 'Alphabetisch nach dem deutschen Titel.'],
]

onMounted(async () => {
  status.value = await api.status()
})
</script>

<template>
  <div class="help">
    <h1>Hilfe</h1>

    <section>
      <h2>Sortierung</h2>
      <dl>
        <template v-for="[name, text] in SORTS" :key="name">
          <dt>{{ name }}</dt>
          <dd>{{ text }}</dd>
        </template>
      </dl>
    </section>

    <section>
      <h2>Filter</h2>
      <dl>
        <dt>Meine Dienste</dt>
        <dd>
          Voreingestellt siehst du nur, was in deinen Abos läuft (Einstellungen → Meine Abos),
          plus die kostenlosen Angebote, falls du sie einbezogen hast. „Alle“ zeigt alles, was
          beobachtet wird, auch Dienste ohne Abo – praktisch vor einem Probemonat. Oder du wählst
          einzelne Dienste aus.
        </dd>
        <dt>Neu: 7 / 14 / 30 Tage</dt>
        <dd>
          Zeigt, was in diesem Zeitraum in einem Dienst dazugekommen ist oder eine neue Staffel
          bekommen hat. Auf den Kacheln steht dann, warum: „Neu bei WOW“ oder „Staffel 3“.
          Grundlage ist der tägliche Abgleich, nicht der offizielle Starttermin. Zusammen mit der
          Sortierung „Zuletzt dazugekommen“ ist das der gespeicherte Filter <strong>Neu</strong>,
          den du beim ersten Start vorfindest – er lässt sich ändern oder löschen wie jeder andere.
        </dd>
        <dt>Altersfreigabe</dt>
        <dd>
          „FSK 12“ ist die deutsche Freigabe. „ab 12*“ mit Sternchen ist aus der US-Freigabe
          geschätzt (z. B. PG-13). Titel ohne jede Angabe werden bei gesetztem Filter ausgeblendet,
          bis du „Titel ohne Angabe anzeigen“ ankreuzt. Die Freigabe sagt nur, ab welchem Alter
          etwas erlaubt ist – nicht, dass es für Kinder gemacht ist.
        </dd>
        <dt>Genre</dt>
        <dd>
          Genres von Filmen und Serien sind zusammengefasst: „Action“ findet auch Serien, die bei
          TMDB unter „Action &amp; Adventure“ laufen. Mehrere Genres wirken als „oder“.
        </dd>
        <dt>Jahr</dt>
        <dd>
          Bezieht sich auf das Erscheinungsjahr bzw. den Serienstart. Serien können älter sein als
          der beobachtete Zeitraum, wenn seitdem neue Folgen liefen.
        </dd>
      </dl>
    </section>

    <section>
      <h2>Wie Neuzugänge erkannt werden</h2>
      <p>
        Einmal am Tag wird für jeden Dienst der aktuelle Katalog geholt und mit dem gespeicherten
        Stand verglichen. Was neu in der Liste steht, ist ein Neuzugang; was fehlt, gilt erst nach
        zwei Tagen in Folge als „nicht mehr im Abo“ – die Daten flackern gelegentlich.
        Der allererste Abgleich ist nur der Ausgangsstand und meldet nichts als neu.
      </p>
      <p>
        Das Datum in der Detailansicht („seit …“) ist der Tag, an dem <em>wir</em> den Titel
        entdeckt haben, nicht der offizielle Starttermin. Ein echtes Startdatum liefern die
        Datenquellen leider nicht.
      </p>
    </section>

    <section>
      <h2>Bewerten</h2>
      <p>
        Auf jeder Kachel: ✓ gesehen, 1–5 Sterne, ✕ nicht interessiert. Eine Sternebewertung
        markiert den Titel automatisch als gesehen, und ein Klick auf denselben Stern nimmt die
        Bewertung wieder zurück. Bewertete und ausgeblendete Titel verschwinden sofort aus der
        Liste; über „Rückgängig“ unten am Bildschirm holst du sie zurück. Gesehene kannst du
        über den Filter wieder einblenden.
      </p>
      <p>
        <strong>Bei Serien geht es auch staffelweise.</strong> In der Detailansicht steht neben
        jeder erschienenen Staffel derselbe ✓-Knopf wie auf der Kachel. Die Serie gilt als gesehen, sobald alle
        erschienenen Staffeln abgehakt sind – und automatisch wieder als offen, sobald eine
        neue dazukommt. So meldet sich eine Serie von selbst zurück, wenn es weitergeht,
        statt für immer ausgeblendet zu bleiben. Das ✓ auf der Kachel hakt weiterhin alles
        auf einmal ab.
      </p>
      <p class="muted">
        Deine Bewertungen sind die Grundlage für die geplante Sortierung „Passt zu mir“.
      </p>
    </section>

    <section>
      <h2>Gespeicherte Filter</h2>
      <p>
        Ein gespeicherter Filter ist eine gespeicherte Suche, z. B. „Animation, bis 6 Jahre“.
        Stelle den Filter auf „Entdecken“ ein und wähle im Menü rechts neben „Filter“ den Eintrag
        <strong>Aktuellen Filter speichern …</strong>.
      </p>
      <p>
        Dasselbe Menü listet alle gespeicherten Filter mit ihrer aktuellen Trefferzahl. Ein Klick
        wendet einen an; der Knopf trägt dann dessen Namen. Änderst du danach etwas, zeigt der Knopf
        einen Punkt, und im Menü stehen <strong>überschreiben</strong> sowie
        <strong>Aktuellen Filter speichern …</strong> für eine neue Kopie. Dort kannst du auch
        umbenennen, löschen oder die Auswahl aufheben.
      </p>
      <p>
        Ein gespeicherter Filter hat keinen eigenen Inhalt: Er zeigt immer, was gerade passt –
        inklusive Neuzugängen.
      </p>
      <p class="muted">
        Kurz: Ein <strong>Filter</strong> ist eine Suche, eine <strong>Liste</strong> eine Sammlung
        von Titeln.
      </p>
    </section>

    <section>
      <h2>Listen</h2>
      <p>
        Das ☆ auf einer Kachel merkt einen Titel in der Standardliste („Merkliste“). Über
        „Listen wählen“ in der Meldung unten oder über die Detailansicht kannst du ihn in
        beliebig viele Listen legen – oder in keine.
      </p>
      <p>
        Unter <RouterLink to="/listen">Listen</RouterLink> verwaltest du sie: anlegen, umbenennen,
        löschen und festlegen, welche die Standardliste ist. Innerhalb einer Liste entfernt das ★
        den Titel aus genau dieser Liste. Titel, die gerade in keinem deiner Dienste laufen,
        bleiben in der Liste und sind als „nicht verfügbar“ markiert; mit „nur verfügbare“
        blendest du sie aus. Gesehenes wird hier nicht automatisch entfernt.
      </p>
    </section>

    <section>
      <h2>Woher die Daten kommen</h2>
      <p>
        Titel, Beschreibungen, Poster und Bewertungen stammen von
        <a href="https://www.themoviedb.org" target="_blank" rel="noopener">TMDB</a>, die
        Verfügbarkeit bei den Diensten von
        <a href="https://www.justwatch.com" target="_blank" rel="noopener">JustWatch</a> (über TMDB).
        Beide Quellen können Lücken haben. Jeder Titel wird zusätzlich einzeln geprüft, damit
        nichts angezeigt wird, das beim Dienst nur zum Leihen oder Kaufen verfügbar ist.
      </p>
      <p v-if="status" class="muted">
        Stand: {{ status.titles }} Titel, davon {{ status.titles_with_age_rating }} mit bekannter
        Altersfreigabe ({{ status.titles_ratings_checked }} abgefragt) und
        {{ status.titles_with_offers }} mit vollständiger Angebotsliste.
        Mehr dazu unter <RouterLink to="/einstellungen">Einstellungen</RouterLink>.
      </p>
    </section>
  </div>
</template>

<style scoped>
.help { max-width: 760px; }
h1 { font-size: 1.4rem; margin: 0 0 8px; }
h2 { font-size: 1.05rem; margin: 30px 0 8px; }
section p { line-height: 1.6; margin: 0 0 10px; }
dl { margin: 0; display: grid; gap: 12px; }
dt { font-weight: 600; }
dd { margin: 3px 0 0; color: var(--text-soft); line-height: 1.55; }
.muted { color: var(--muted); font-size: .9rem; }
</style>

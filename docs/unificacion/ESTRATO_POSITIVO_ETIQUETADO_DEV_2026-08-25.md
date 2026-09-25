# ESTRATO POSITIVO — ETIQUETADO bajo criterio ratificado del propietario (DEV, 2026-08-25)

> **Fuente:** `ESTRATO_POSITIVO_CANDIDATOS_DEV_2026-08-25.md` (222 candidatos, modos B/C/D/E/F/M; A vacío).
> **Etiquetador:** agente, por delegación ratificada (§16.1 de `ESTADO_Y_HOJA_DE_RUTA.md`).
> **Conflicto declarado:** el agente ha visto el holdout (mismo caso que development-2/3). El gate
> solo puntúa el holdout congelado; este etiquetado alimenta EXCLUSIVAMENTE development.

## 0. Criterios aplicados (con su fuente ratificada)

| # | Criterio | Fuente |
|---|----------|--------|
| C1 | Variantes de redacción de empresa o título = MISMA oferta («Kanton Zug»↔«Kantonale Verwaltung Zug», «Universität Basel»↔«University of Basel», «Stadt»↔«Stadtverwaltung», traducción DE↔EN del título) | §16.1; TPOS-01/02/04, D2A-08/16/26/27 |
| C2 | Mismo texto con ciudad/ubicación concreta DISTINTA = DOS ofertas (multi-ciudad ⇒ distintas) | dev-3 (E-INTRA taxtalente, VETO-01..06, D2A-30); acta holdout 2026-08-24 |
| C3 | Husos horarios (CET/CEST/UTC/GMT/CST…) en la ubicación = remoto | Track R fase 3 (§17.4) |
| C4 | Remoto solo casa con remoto SALVO evidencia explícita de mismo puesto anunciado dos veces; los C exigen: desc idéntica + misma empresa + mismo rol | §17.3 (rama remoto↔concreto); XVETO-01 |
| C4b | Mismo puesto REMOTO publicado por región/lista de estados solapada = MISMA oferta (remoto~remoto) | IPOS-06 (dev3_v3, «mismo puesto remoto publicado por region») |
| C5a | Mismo puesto con/sin rango de pensum, o rangos solapados (50%–60% vs 50%; 80–100% vs sin) = MISMA oferta | IHARD-05 (dev3_v3, duplicate ratificado) |
| C5b | Pensums claramente DISJUNTOS (55% vs 27%) = DOS plazas | IPOS-03 (dev3_v2, distinct ratificado) — ver conflicto en §3 |
| C6 | Plantillas idénticas con ROL DISTINTO (TELUS por idioma; lemon.io por stack; Project vs Product; nivel Trainee/Junior/Senior vs regular; especialización M2M vs MCPTT; unidades/programas distintos) = DISTINTAS | dev-3 (IHARD-01/-06, E-REMOTO-03, XPOS-08/FPOS-06, D2A-12/15/25) |
| C7 | Empresa escrita distinto pero misma entidad («remotecom»↔«Remote», «Proxify»↔«Proxify AB») = misma empresa | delegación; XPOS-02/FPOS-02, D2A-05 |
| C8 | Ubicaciones jerárquicamente compatibles no son multi-ciudad (Emmen ∈ LU; Texas ∈ USA; Wallisellen ~ Zürich) | XPOS-01/03, D2A-02/07, IPOS-01 v3 |

**Método:** solo la evidencia del fichero de candidatos + precedentes ratificados par a par
(varios candidatos F son EXACTAMENTE pares ya adjudicados en dev-3 v3 y se citan). Evidencia
insuficiente o criterios en conflicto ⇒ `ambiguous-owner`, jamás se inventa contenido.

## 1. Tabla resumen por modo

| Modo | Candidatos | duplicate | distinct | ambiguous-owner |
|------|-----------:|----------:|---------:|----------------:|
| B — intra-fuente, trgm [0.60,0.90) | 60 | 45 | 5 | 10 |
| C — remoto ↔ concreto | 26 | 5 | 18 | 3 |
| D — cross-portal bajo trgm 0.65 | 60 | 3 | 55 | 2 |
| E — sin token de empresa común | 27 | 2 | 24 | 1 |
| F — embedding [0.85,0.95) cross | 42 | 31 | 8 | 3 |
| M — control multi-ciudad | 7 | 0 | **7** | 0 |
| **Total** | **222** | **86** | **117** | **19** |

**Autocomprobación M:** 7/7 `distinct` — la regla multi-ciudad ratificada decide los 7 controles
(M-07 es además el par ya adjudicado D2A-30, Lidl Schänis vs Flums, distinct).

## 2. Etiquetado completo

### Modo B — intra-fuente, hash distinto (60)

| Par | Lados | Veredicto | Criterio aplicado |
|-----|-------|-----------|-------------------|
| B-01 | `6c18a1c0`↔`d16b64be` | duplicate | C1: título recortado (sin «AXIOM &»), misma empresa/ciudad, desc 0.96 + mismo salario |
| B-02 | `86d223d6`↔`baf4db8f` | distinct | C6: Senior vs regular = nivel distinto (IHARD-01/-06), pese a desc 1.0 y mismo salario |
| B-03 | `34f53b19`↔`5f6f9d33` | duplicate | C5a: pensum 50%–60% vs 50% solapado, mismo puesto (IHARD-05) |
| B-04 | `5a5759a1`↔`d076d031` | duplicate | C1: título recortado (HCM PY→HCM), misma empresa, desc 0.84 |
| B-05 | `4b1eeb44`↔`9bde2ccc` | duplicate | C1: calificador «ArbR» añadido, desc 1.0, misma empresa/ciudad |
| B-06 | `7bd9010f`↔`9bde2ccc` | duplicate | C1: ídem B-05 (repost del mismo anuncio) |
| B-07 | `9acc745f`↔`9bde2ccc` | duplicate | C1: ídem B-05 (repost del mismo anuncio) |
| B-08 | `09a9b4e4`↔`42923152` | duplicate | C1: «Netzwerk-Administration»↔«Netzwerk- / IT-Administration», desc 1.0 |
| B-09 | `73fb5569`↔`c78846ad` | duplicate | C1: título idéntico, desc 1.0, misma empresa/ciudad (repost) |
| B-10 | `20a5a30d`↔`4f432cf8` | distinct | C6: Senior vs regular = nivel distinto (IHARD-01/-06) |
| B-11 | `4f432cf8`↔`5d0d9767` | distinct | C6: Senior vs regular = nivel distinto |
| B-12 | `da596354`↔`fef71447` | ambiguous-owner | C5b vs enunciado delegado: pensums disjuntos 80% vs 60% (ver §3) |
| B-13 | `8dd66df6`↔`a3d5d33c` | ambiguous-owner | C5b vs enunciado delegado: pensums disjuntos 60% vs 80% (ver §3) |
| B-14 | `8dd66df6`↔`da596354` | ambiguous-owner | C5b vs enunciado delegado: pensums disjuntos 60% vs 80% (ver §3) |
| B-15 | `a3d5d33c`↔`fef71447` | ambiguous-owner | C5b vs enunciado delegado: pensums disjuntos 80% vs 60% (ver §3) |
| B-16 | `5d7e1822`↔`741e2f0d` | duplicate | C1: etiqueta de cuenta «- Ikea» añadida, desc 1.0, sin variantes hermanas en evidencia |
| B-17 | `06f77119`↔`c058d85c` | duplicate | C1: «Tech» añadido al mismo título, desc 1.0 |
| B-18 | `c058d85c`↔`d3792aad` | duplicate | C1: ídem B-17 |
| B-19 | `80f6b599`↔`b15ab0be` | duplicate | C1: artículo «eine» añadido, desc 1.0 |
| B-20 | `a5930e4c`↔`b4387f86` | ambiguous-owner | PST vs EST: ¿rol distinto por franja (analogía TELUS, C6) o huso=remoto ⇒ misma (C3)? — los criterios no lo deciden |
| B-21 | `2aa5620e`↔`cac3021f` | distinct | C6: ML Research vs Evals = especialización distinta (precedente M2M vs MCPTT) |
| B-22 | `14cd4ad1`↔`156f7937` | duplicate | C5a: con/sin pensum 60%, mismo puesto SSBL LU (IHARD-05, análogo D2A-07) |
| B-23 | `b74bc004`↔`c189a219` | duplicate | C5a: pensum 60% vs 60–80% solapado (IHARD-05) |
| B-24 | `47ba818d`↔`4c0e69ab` | duplicate | C4b: mismo puesto remoto/telehealth publicado por listas de estados solapadas (IPOS-06) |
| B-25 | `2024f419`↔`47ba818d` | duplicate | C4b: ídem clúster Legion Health (IPOS-06) |
| B-26 | `4eeaf178`↔`5933458d` | duplicate | C1: prefijo decorativo «Zz» (precedente «Eks:», IPOS-03 v3) — registros de test sintéticos |
| B-27 | `5933458d`↔`a3be1ca4` | duplicate | C1: ídem B-26 |
| B-28 | `5933458d`↔`c432c604` | duplicate | C1: ídem B-26 |
| B-29 | `43d176a1`↔`47ba818d` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-30 | `47ba818d`↔`934b6789` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-31 | `22ba28dc`↔`47ba818d` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-32 | `4982c163`↔`78e3dbe3` | duplicate | C4b: Staff Psychiatrist remoto por estados solapados (IPOS-06) |
| B-33 | `75fea67f`↔`df645ffb` | ambiguous-owner | C5b vs enunciado delegado: pensums disjuntos 50% vs 80% (ver §3) |
| B-34 | `2f086a1f`↔`9ceae345` | duplicate | C4b: Collaborating Psychiatrist remoto por estados solapados (IPOS-06) |
| B-35 | `2f086a1f`↔`f9f03138` | duplicate | C4b: ídem B-34 |
| B-36 | `126d4b29`↔`9f6ec297` | ambiguous-owner | agencia de colocación con desc muy divergente (0.387): ¿variante de redacción (C1) o dos mandatos de cliente? — la evidencia no lo decide |
| B-37 | `28fa2375`↔`6ced647f` | ambiguous-owner | ídem B-36 |
| B-38 | `6ced647f`↔`9f6ec297` | ambiguous-owner | ídem B-36 |
| B-39 | `126d4b29`↔`28fa2375` | ambiguous-owner | ídem B-36 |
| B-40 | `07d000e7`↔`7b27cd33` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-41 | `ad4a2311`↔`fdf1c0ce` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-42 | `487331eb`↔`669ad0bd` | duplicate | C5a: con/sin «80-100%», mismo puesto SSBL Knutwil (IHARD-05) |
| B-43 | `47ba818d`↔`bf3ee9e0` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-44 | `07d000e7`↔`47ba818d` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-45 | `4c0e69ab`↔`dc8dd240` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-46 | `47ba818d`↔`dc8dd240` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-47 | `2024f419`↔`dc8dd240` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-48 | `4c0e69ab`↔`edcac1b3` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-49 | `47ba818d`↔`edcac1b3` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-50 | `2024f419`↔`edcac1b3` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-51 | `4c0e69ab`↔`fdf1c0ce` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-52 | `47ba818d`↔`fdf1c0ce` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-53 | `2024f419`↔`fdf1c0ce` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-54 | `04176dec`↔`4a6be590` | duplicate | C5a: con/sin «80-100%», mismo puesto SSBL LU (IHARD-05) |
| B-55 | `bdcb680f`↔`e3b15f31` | duplicate | C1: «(SH & KES)»↔«WSH / KES» variante de siglas, misma empresa/ciudad |
| B-56 | `68c30f8a`↔`6da0a6a0` | distinct | C6: porteføljestyring vs økonomistyring = especialización distinta, desc 0.419 |
| B-57 | `47ba818d`↔`b1c9ab88` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-58 | `47ba818d`↔`ad4a2311` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-59 | `43d176a1`↔`dc8dd240` | duplicate | C4b: clúster Legion Health (IPOS-06) |
| B-60 | `2024f419`↔`7b27cd33` | duplicate | C4b: clúster Legion Health (IPOS-06) |

### Modo C — gemelos remoto ↔ concreto (26)

| Par | Lados | Veredicto | Criterio aplicado |
|-----|-------|-----------|-------------------|
| C-01 | `080c877c`↔`7b8f6464` | ambiguous-owner | C4 exige misma empresa + desc idéntica: empresa VACÍA en jobspresso y desc 0.799 — evidencia insuficiente |
| C-02 | `7a06338e`↔`d3bb4b09` | duplicate | C4: desc idéntica (1.0) + misma empresa + mismo rol — mismo puesto anunciado UK/Remote |
| C-03 | `4af56823`↔`54f6395f` | duplicate | C4: desc 1.0 + misma empresa + mismo rol — Remote/Berlin |
| C-04 | `bb25a69a`↔`ea71d0ee` | distinct | C2/C6: países distintos (Canada vs Japan) — rol por país tipo TELUS, multi-ubicación ⇒ distintas |
| C-05 | `2730200f`↔`ea71d0ee` | distinct | C2/C6: Canada vs Japan |
| C-06 | `be00bf0d`↔`f117dd49` | distinct | C2: UK vs South Africa; desc 0.864 no alcanza la evidencia C4 |
| C-07 | `515990cd`↔`bb25a69a` | distinct | C2/C6: Japan vs Canada |
| C-08 | `25d22640`↔`bb25a69a` | distinct | C2/C6: Japan vs Canada |
| C-09 | `22df3608`↔`8e0c4096` | duplicate | C4/C8: desc 1.0 + misma empresa + mismo rol; «Guatemala, South Africa» ⊂ «Global» |
| C-10 | `2730200f`↔`515990cd` | distinct | C2/C6: Canada vs Japan |
| C-11 | `515990cd`↔`6472dfbd` | distinct | C2/C6: Japan vs Canada |
| C-12 | `25d22640`↔`6472dfbd` | distinct | C2/C6: Japan vs Canada |
| C-13 | `9fde62a6`↔`e972d234` | distinct | C2/C6: Japan vs Canada |
| C-14 | `515990cd`↔`e972d234` | distinct | C2/C6: Japan vs Canada |
| C-15 | `25d22640`↔`e972d234` | distinct | C2/C6: Japan vs Canada |
| C-16 | `bb25a69a`↔`f0dd9574` | distinct | C2/C6: Canada vs Sweden |
| C-17 | `2730200f`↔`f0dd9574` | distinct | C2/C6: Canada vs Sweden |
| C-18 | `0c4fbbb9`↔`97eea7ba` | duplicate | C4: desc 1.0 + misma empresa + mismo rol — Leipzig/Remote |
| C-19 | `25d22640`↔`2730200f` | distinct | C2/C6: Japan vs Canada |
| C-20 | `6472dfbd`↔`9fde62a6` | distinct | C2/C6: Canada vs Japan |
| C-21 | `9fde62a6`↔`bb25a69a` | distinct | C2/C6: Japan vs Canada |
| C-22 | `365aa3d6`↔`5cc23004` | duplicate | C3+C4: CST en ambos lados ⇒ remoto~remoto; desc 1.0 + misma empresa + mismo rol |
| C-23 | `2500cca5`↔`49109d9c` | ambiguous-owner | C4 exige desc idéntica y el fichero no trae desc_sim (jobgether trunca desc); empresa y título idénticos pero la evidencia C está incompleta |
| C-24 | `49109d9c`↔`d2bc1ae1` | ambiguous-owner | ídem C-23 |
| C-25 | `62378861`↔`8eaad4d7` | distinct | C6: Senior vs regular + desc 0.356 + remoto_xor sin evidencia C4 |
| C-26 | `8eaad4d7`↔`d543ecba` | distinct | C6: ídem C-25 (desc 0.323) |

### Modo D — variantes cross-portal bajo trgm 0.65 (60)

| Par | Lados | Veredicto | Criterio aplicado |
|-----|-------|-----------|-------------------|
| D-01 | `4b8721b4`↔`e11e6ea1` | ambiguous-owner | TELUS: «English Speakers» vs «Canada» SIN marca de idioma — existen variantes FR/ES hermanas, el lado sin marca no es adjudicable |
| D-02 | `22bf51e2`↔`e11e6ea1` | distinct | C2/C6: USA-español vs Canada = país y mercado distintos (TELUS) |
| D-03 | `01332292`↔`4b8721b4` | distinct | C6: French vs English = rol TELUS por idioma ⇒ distintas |
| D-04 | `4b8721b4`↔`dbe79266` | distinct | C6: English vs French (C) ⇒ distintas |
| D-05 | `370e70ce`↔`a0227962` | distinct | C6: programas SSBL distintos (Wohnen Vielfalt vs Wohnen Struktur) |
| D-06 | `0267d93c`↔`4b8721b4` | distinct | C6: español-USA vs English-Canada = idioma y país distintos |
| D-07 | `9589f485`↔`e89389a0` | distinct | C6: Wohnen Pflege vs Wohnen Struktur = programas distintos |
| D-08 | `2531f3d3`↔`4b48b72c` | distinct | C6: Struktur vs Pflege |
| D-09 | `14676a80`↔`4b48b72c` | distinct | C6: Vielfalt vs Pflege |
| D-10 | `7b8f6464`↔`ff48c803` | duplicate | C1/C7: mismo rol TELUS Content Reviewer US, «United States»↔«US» (precedente QA Rater DE↔Germany dup con desc baja) |
| D-11 | `0cfd4b12`↔`99c2c749` | duplicate | C1: «Pastoral Care»↔«Pastoral & Wellbeing», misma escuela (precedente D2A-11) |
| D-12 | `14676a80`↔`e89389a0` | distinct | C6: Vielfalt vs Struktur |
| D-13 | `370e70ce`↔`867ab481` | distinct | C6: Vielfalt vs Struktur |
| D-14 | `a78ef16e`↔`f117dd49` | distinct | C6: Business Ops vs AI Ops = roles distintos |
| D-15 | `07b4db31`↔`9589f485` | distinct | C6: Struktur vs Pflege |
| D-16 | `14676a80`↔`8e63263e` | distinct | C6: Vielfalt vs Pflege |
| D-17 | `2531f3d3`↔`8e63263e` | distinct | C6: Struktur vs Pflege |
| D-18 | `5d401766`↔`befb7ac0` | distinct | C6: Product Engineer vs ML Research = roles distintos (precedente lemon.io) |
| D-19 | `922bf5b6`↔`befb7ac0` | distinct | C6: ídem D-18 |
| D-20 | `98a9751f`↔`befb7ac0` | distinct | C6: ídem D-18 |
| D-21 | `378074c5`↔`a0227962` | distinct | C6: centro/programa distinto (HF/FH Hitzkirch vs Wohnen Struktur) |
| D-22 | `19be22e0`↔`a0227962` | distinct | C6/C2: centro distinto (Schüpfheim vs programa Struktur) |
| D-23 | `26ddccfb`↔`5df05167` | distinct | C6: FaGe (Gesundheit) vs FaBe (Betreuung) = profesiones distintas |
| D-24 | `5df05167`↔`df06b96a` | distinct | C6: ídem D-23 |
| D-25 | `a0227962`↔`ee5ac1b0` | distinct | C6: Struktur vs Pflege |
| D-26 | `07b4db31`↔`14676a80` | distinct | C6: Struktur vs Vielfalt |
| D-27 | `867ab481`↔`ee5ac1b0` | distinct | C6: Struktur vs Pflege |
| D-28 | `1cbf5184`↔`ac751984` | distinct | C6: HR vs Assessment = especializaciones distintas |
| D-29 | `93337aaa`↔`f25a18e5` | distinct | C6: Stv. Teamleitung vs Co-Leitung = cargos distintos (precedente Senior AM vs Key AM) |
| D-30 | `4cf53f3f`↔`f25a18e5` | distinct | C6: ídem D-29 |
| D-31 | `afdc33ea`↔`f1fd3633` | distinct | C6: Head of Primary vs Head of Music = roles distintos |
| D-32 | `110bc5f9`↔`36401b9b` | distinct | C6: AE EMEA vs Renewal AE CML = roles distintos |
| D-33 | `beb99b93`↔`e89389a0` | distinct | C6: Sozialpädagoge (HF/FH) vs FaBe/FaGe (EFZ) = cualificación distinta, mismo programa |
| D-34 | `2531f3d3`↔`a0227962` | distinct | C6: ídem D-33 |
| D-35 | `4b48b72c`↔`ee5ac1b0` | distinct | C6: FaGe/FaBe vs Pflegefachperson/Sozialpädagoge = cualificación distinta |
| D-36 | `aef602c7`↔`c469eb35` | distinct | C6: Leitung Schulinsel vs Haushelfer = roles distintos |
| D-37 | `14cd4ad1`↔`55a20e27` | distinct | C6: Mitarbeiter vs Teamleitung = nivel distinto |
| D-38 | `14cd4ad1`↔`a7de72a9` | distinct | C6: ídem D-37 |
| D-39 | `07b4db31`↔`beb99b93` | distinct | C6: FaBe/FaGe vs Sozialpädagoge = cualificación distinta |
| D-40 | `14cd4ad1`↔`7b1f4a53` | distinct | C6: Nachtdienst vs Servicemitarbeiter = roles distintos |
| D-41 | `7b1f4a53`↔`ea1b9725` | distinct | C6: ídem D-40 |
| D-42 | `2531f3d3`↔`867ab481` | distinct | C6: FaBe/FaGe vs Sozialpädagoge = cualificación distinta |
| D-43 | `3d37d226`↔`ac751984` | distinct | C6: Sucht vs Assessment = especializaciones distintas |
| D-44 | `79332a22`↔`7b1f4a53` | distinct | C6: Nachtdienst vs Servicemitarbeiter |
| D-45 | `91bf43b1`↔`aef602c7` | ambiguous-owner | Schulinsel genérico vs «Steingut»: existe también «Alpenblick» (D-52) — el genérico no es asignable con la evidencia del fichero |
| D-46 | `0c4b253b`↔`4cf53f3f` | distinct | C6: Tagesstätte vs Bereich Wohnen = unidades distintas + cargo distinto |
| D-47 | `66a6f396`↔`93337aaa` | distinct | C6: ídem D-46 |
| D-48 | `4cf53f3f`↔`66a6f396` | distinct | C6: ídem D-46 |
| D-49 | `0c4b253b`↔`93337aaa` | distinct | C6: ídem D-46 |
| D-50 | `156f7937`↔`55a20e27` | distinct | C6: Mitarbeiter vs Teamleitung |
| D-51 | `156f7937`↔`a7de72a9` | distinct | C6: ídem D-50 |
| D-52 | `aef602c7`↔`c8848f76` | distinct | C6: Schulinsel Steingut vs Alpenblick = unidades distintas |
| D-53 | `8e63263e`↔`ee5ac1b0` | distinct | C6: FaGe/FaBe vs Pflegefachperson/Sozialpädagoge = cualificación distinta |
| D-54 | `0cfd4b12`↔`2662e136` | distinct | C6: Pastoral Care vs Head of Primary = roles distintos |
| D-55 | `156f7937`↔`7b1f4a53` | distinct | C6: Nachtdienst vs Servicemitarbeiter |
| D-56 | `a99e4224`↔`bdb34d13` | distinct | C6: Deployment Manager vs Strategic Account Manager |
| D-57 | `752ad3cf`↔`e4a03747` | distinct | C6: roles distintos + desc 0.241 |
| D-58 | `7b8f6464`↔`8776f48a` | duplicate | C1: mismo rol/mercado TELUS US-inglés («United States»↔«English US»); desc baja no bloquea (precedente QA Rater desc 0.199 dup) |
| D-59 | `752ad3cf`↔`b7d0eca0` | distinct | C6: Account Manager vs Client Success Manager |
| D-60 | `752ad3cf`↔`fc85cec2` | distinct | C6: ídem D-59 |

### Modo E — sin token de empresa común (27)

| Par | Lados | Veredicto | Criterio aplicado |
|-----|-------|-----------|-------------------|
| E-01 | `e354f111`↔`eda5682e` | duplicate | C7: «remotecom»↔«Remote» misma entidad + título idéntico distintivo |
| E-02 | `ce80fff1`↔`eda5682e` | duplicate | C7: ídem E-01 (repost) |
| E-03 | `04f4f5cf`↔`7b0a69ee` | distinct | empresas DISTINTAS (Clerk.io vs Arketa) |
| E-04 | `14cd4ad1`↔`b8921a22` | distinct | empresas distintas (SSBL vs Landscheide) |
| E-05 | `0e1ca6fb`↔`14cd4ad1` | distinct | empresas distintas (Murimoos vs SSBL) |
| E-06 | `970dd6ae`↔`a5a7e5d1` | ambiguous-owner | no consta en la evidencia si «Mosaic Ecole» e «International School of Central Switzerland» son la misma entidad |
| E-07 | `156f7937`↔`b8921a22` | distinct | empresas distintas (SSBL vs Landscheide) |
| E-08 | `1534ec47`↔`f6bb68a4` | distinct | empresas distintas (Fireblocks vs Zinier) + EMEA vs US |
| E-09 | `15a44c60`↔`e44b7387` | distinct | empresas distintas (GPC vs Langan), desc 0.227 |
| E-10 | `1528ae37`↔`27994705` | distinct | empresas distintas (Proxify vs OnTheGoSystems), desc 0.206 |
| E-11 | `27994705`↔`27d615a5` | distinct | ídem E-10 |
| E-12 | `46746ac9`↔`718feaa8` | distinct | empresas distintas (PM Group vs Langan) |
| E-13 | `3037ecf4`↔`53c77557` | distinct | empresas distintas (ProWriterSites vs IAPWE) |
| E-14 | `15a44c60`↔`1797c9eb` | distinct | empresas distintas (GPC vs Langan) |
| E-15 | `15a44c60`↔`58873447` | distinct | ídem E-14 |
| E-16 | `15a44c60`↔`68a4022a` | distinct | ídem E-14 |
| E-17 | `84620761`↔`9cd303eb` | distinct | empresas distintas (Obesity Society vs Accpro) + USA vs Dublin |
| E-18 | `718feaa8`↔`aab8104f` | distinct | empresas distintas (Langan vs Mott MacDonald) |
| E-19 | `1534ec47`↔`19ede652` | distinct | empresas distintas (Fireblocks vs AON) |
| E-20 | `2ff668b0`↔`be850780` | distinct | C6: Senior vs regular + empresa vacía en jobspresso (precedente D2B-14: distinct) |
| E-21 | `9bbc72db`↔`d98a8261` | distinct | empresas distintas (Langan vs Vickerstock) + Senior vs regular |
| E-22 | `16c3db48`↔`55e0c321` | distinct | empresas distintas (GPC vs Langan) + nivel |
| E-23 | `16c3db48`↔`9bbc72db` | distinct | ídem E-22 |
| E-24 | `55e0c321`↔`d98a8261` | distinct | empresas distintas (Langan vs Vickerstock) |
| E-25 | `d1614d47`↔`d98a8261` | distinct | ídem E-24 + Graduate vs regular |
| E-26 | `16c3db48`↔`d1614d47` | distinct | empresas distintas + Graduate vs regular |
| E-27 | `5f52cedf`↔`f96464fd` | distinct | empresas distintas (ALM Corp vs Evolve Digital) |

### Modo F — banda de embedding [0.85, 0.95) cross-source (42)

| Par | Lados | Veredicto | Criterio aplicado |
|-----|-------|-----------|-------------------|
| F-01 | `21a3204c`↔`79f0e69c` | duplicate | C1: «Universität Basel»↔«University of Basel» — par ya adjudicado TPOS-02 (dup) |
| F-02 | `669ad0bd`↔`af124cfd` | duplicate | C5a: con/sin «80-100%», mismo puesto SSBL Knutwil (IHARD-05) |
| F-03 | `15eeba4d`↔`6973e1ba` | duplicate | C1: «Stadt Kriens»↔«Stadtverwaltung Kriens», título idéntico |
| F-04 | `1af81f9c`↔`eee089c9` | duplicate | C1: par ya adjudicado TPOS-01/D2A-08 (dup) |
| F-05 | `5a08c395`↔`e12b4107` | duplicate | C1: par ya adjudicado TPOS-04/D2A-26 (dup) |
| F-06 | `def34852`↔`e1e21724` | distinct | C6: lemon.io Graphic vs UI&UX/Graphic = clase FP ratificada de roles lemon.io (XPOS-08, D2A-15/25) |
| F-07 | `def34852`↔`f2019df5` | distinct | C6: ídem F-06 |
| F-08 | `114520b9`↔`93337aaa` | duplicate | C1: título idéntico, misma empresa, Zürich=Zürich, cross-portal |
| F-09 | `41c8fe54`↔`93337aaa` | duplicate | C1: ídem F-08 (repost schuljobs) |
| F-10 | `4cf53f3f`↔`c25f95d9` | duplicate | C1: ídem F-08 |
| F-11 | `93337aaa`↔`c25f95d9` | duplicate | C1: ídem F-08 |
| F-12 | `bcb45bf0`↔`ed14c709` | duplicate | C1: «Kantonale Verwaltung Zug»↔«Kanton Zug» — el ejemplar ratificado §16.1 |
| F-13 | `3ef10fa1`↔`d5606b1c` | duplicate | C1: ídem F-12 |
| F-14 | `607d2a80`↔`7a285613` | distinct | empresas distintas (Pragmatike vs Diligent) |
| F-15 | `0267d93c`↔`22bf51e2` | duplicate | C1: mismo rol TELUS español-USA, «speakers»↔«Speaker» |
| F-16 | `0267d93c`↔`fb423d80` | duplicate | C1/C8: mismo rol; Texas ⊂ USA |
| F-17 | `5df05167`↔`6393441f` | duplicate | C1: título idéntico, misma empresa, Zürich, cross-portal |
| F-18 | `5df05167`↔`68eb6b75` | duplicate | C1: ídem F-17 |
| F-19 | `5df05167`↔`aac43806` | duplicate | C1: ídem F-17 |
| F-20 | `b30c1a38`↔`e9b1a1de` | duplicate | C7: «nozominetworks»↔«Nozomi Networks» + título idéntico |
| F-21 | `35c701de`↔`ef4dfb00` | duplicate | C1: par ya adjudicado XPOS-05/FPOS-04 (dup) — desc 0.199 no bloquea |
| F-22 | `4d7c9acb`↔`ef4dfb00` | duplicate | C1: par ya adjudicado XPOS-07/FPOS-05 (dup) |
| F-23 | `ef4dfb00`↔`fcb036b5` | duplicate | C1: mismo clúster QA Rater alemán (otra publicación) |
| F-24 | `07b4db31`↔`2531f3d3` | duplicate | C1/C8: par ya adjudicado FPOS-01/D2A-02 (dup) — Emmen ∈ LU |
| F-25 | `19ed47ea`↔`378074c5` | duplicate | C5a: con/sin «70-100%», mismo puesto SSBL Hitzkirch (IHARD-05) |
| F-26 | `378074c5`↔`a1ee6974` | duplicate | C5a: ídem F-25 |
| F-27 | `8e63263e`↔`9589f485` | duplicate | C1: par ya adjudicado FPOS-08/D2A-29 (dup) |
| F-28 | `080c877c`↔`ff48c803` | ambiguous-owner | empresa VACÍA en jobspresso: la evidencia C4 (misma empresa) no es verificable; desc 0.604 |
| F-29 | `45886657`↔`7569bf31` | distinct | fundaciones distintas (Seefeld vs Altried) — patrón D2A-01 |
| F-30 | `650a4225`↔`f1fd3633` | duplicate | C1: par ya adjudicado FPOS-07/XPOS-09 (dup, «mismo puesto de música reordenado») |
| F-31 | `1d55a4f3`↔`3bea9fd2` | duplicate | C1: «Bundesverwaltung»↔«Swiss Federal Administration» + título contenido (concatenación del scraper) |
| F-32 | `cc7b1566`↔`d966feb3` | distinct | empresas distintas (Ergotopia vs Conceptboard) |
| F-33 | `1528ae37`↔`9a09ba2c` | duplicate | C7: par ya adjudicado XPOS-02/FPOS-02/D2A-05 (dup, Proxify=Proxify AB) |
| F-34 | `27d615a5`↔`9a09ba2c` | duplicate | C7: mismo clúster Proxify RoR (repost workingnomads) |
| F-35 | `69d0576d`↔`bedcbd7f` | distinct | empresas distintas (sparetech vs 6sense) |
| F-36 | `bedcbd7f`↔`c9de1f8a` | distinct | ídem F-35 |
| F-37 | `458130b6`↔`766669cc` | distinct | empresas distintas (Cognite vs FRS), desc 0.066 |
| F-38 | `4dbc5f48`↔`b6bd8154` | duplicate | C7: Proxify=Proxify AB + título idéntico (patrón D2A-05) |
| F-39 | `b6bd8154`↔`db9b32e1` | duplicate | C7: ídem F-38 |
| F-40 | `79f0e69c`↔`c4c618a3` | duplicate | C1: título traducido DE↔EN del mismo puesto «(open rank)», misma entidad (clúster TPOS-02) |
| F-41 | `0b1cc161`↔`1528ae37` | ambiguous-owner | Proxify: ¿«(AI-Augmented Engineering)» es variante de redacción del mismo listing o listing distinto? desc 0.227 — sin precedente que lo decida |
| F-42 | `0b1cc161`↔`27d615a5` | ambiguous-owner | ídem F-41 |

### Modo M — control multi-ciudad (7)

| Par | Lados | Veredicto | Criterio aplicado |
|-----|-------|-----------|-------------------|
| M-01 | `01c5d762`↔`56d75d86` | distinct | C2: Hitzkirch vs Schüpfheim — multi-ciudad ⇒ distintas |
| M-02 | `093c06da`↔`4be4885c` | distinct | C2: Hitzkirch vs Schüpfheim |
| M-03 | `24a7b6b4`↔`378074c5` | distinct | C2: Schüpfheim vs Hitzkirch |
| M-04 | `4be4885c`↔`a9af1a4e` | distinct | C2: Schüpfheim vs Hitzkirch |
| M-05 | `00c1c9c6`↔`7f7dcebb` | distinct | C2: Lidl Lüchingen vs Sursee — filiales distintas |
| M-06 | `2c36ba12`↔`7f7dcebb` | distinct | C2: Lidl Landquart vs Sursee |
| M-07 | `b981f388`↔`c31a056d` | distinct | C2: par ya adjudicado D2A-30/XVETO-02 (distinct, Lidl Schänis vs Flums) |

## 3. AMBIGUOUS-OWNER — 19 pares para el propietario

> Solo cuando los criterios ratificados no deciden o la evidencia del fichero es insuficiente.

**⚠ Conflicto de criterio detectado (pensums disjuntos) — 6 pares.** El enunciado de la
delegación dice «pensums distintos (80% vs 100%) del mismo puesto = MISMA oferta (IPOS-03)»,
pero el acta ratificada dice lo contrario: IPOS-03 (dev3_v2) = «pensums DISTINTOS (55% vs 27%)
= dos plazas» ⇒ **distinct**, y lo que sí es duplicate ratificado es el caso con/sin RANGO
solapado (IHARD-05, y el ex-positivo de test «80–100 vs 80»). Los solapados se han etiquetado
`duplicate` (B-03, B-22, B-23, B-42, B-54, F-02, F-25, F-26); los DISJUNTOS van al propietario:

| Par | Caso | Por qué no decide |
|-----|------|-------------------|
| B-12, B-13, B-14, B-15 | Schulheim Elgg, Sozialpädagog*in 80% vs 60% (desc 1.0) | pensums disjuntos: IPOS-03 ⇒ dos plazas; el enunciado delegado ⇒ misma oferta |
| B-33 | AOZ, Fallverantwortliche*r MNA 50% vs 80% | ídem |
| B-20 | Grafana, Observability Architect **PST** vs **EST** | ¿rol distinto por franja horaria (analogía TELUS por idioma ⇒ distintas) o huso=remoto ⇒ misma oferta? |
| B-36, B-37, B-38, B-39 | MY Humancapital (agencia), (IT-)Systemadministrator, desc 0.387 | variante de redacción ⇒ misma vs desc muy divergente en agencia multi-cliente ⇒ dos mandatos |
| C-01 | jobspresso (empresa vacía) vs TELUS remotive, Content Reviewer US, desc 0.799 | la evidencia C exige misma empresa + desc idéntica; la empresa falta y la desc no es idéntica |
| C-23, C-24 | Ifblueprint/IF-Blueprint, Dynamics 365 (gn), arbeitnow vs jobgether | empresa y título idénticos pero sin desc_sim en el fichero (jobgether trunca): la evidencia C está incompleta |
| D-01 | TELUS «English Speakers» vs «Canada» (sin idioma) | existen variantes FR/ES hermanas: el lado sin marca de idioma no es adjudicable |
| D-45 | Stadt Schaffhausen, Schulinsel genérico vs «Steingut» | también existe «Alpenblick»: el genérico no es asignable |
| E-06 | Mosaic Ecole vs International School of Central Switzerland | no consta si son la misma entidad |
| F-28 | jobspresso (empresa vacía) vs TELUS weworkremotely | como C-01: misma empresa no verificable, desc 0.604 |
| F-41, F-42 | Proxify RoR vs RoR «(AI-Augmented Engineering)», desc 0.227 | ¿variante de redacción o listing distinto del marketplace? sin precedente |

## 4. Notas de etiquetado (trazabilidad)

- **Clúster Legion Health (25 pares B):** etiquetado `duplicate` por IPOS-06 («mismo puesto
  remoto publicado por region», ratificado dup): título remoto/telehealth idéntico y listas de
  estados que se SOLAPAN (no cumplen `loc_incompatible`; no son el caso M). Si el propietario
  considera que la licencia por estado hace de cada lista una plaza, el clúster entero pasaría
  a distinct — decisión reversible en bloque.
- **B-26/27/28** son registros de TEST sintéticos («SkipDedup Collapse Test», Clera): se
  etiquetan por criterio (prefijo decorativo ⇒ dup, precedente «Eks:»), pero conviene
  EXCLUIRLOS del estrato antes de congelarlo — no son ofertas reales.
- 11 pares F son exactamente pares ya adjudicados en dev-3 v3 (TPOS/FPOS/XPOS): el veredicto
  ratificado se replica, no se re-decide.
- Ningún candidato del fichero fue modificado; este documento es el único artefacto nuevo.

## 5. JSON máquina-legible (pair_id → label)

```json
{
  "B-01": "duplicate", "B-02": "distinct", "B-03": "duplicate", "B-04": "duplicate",
  "B-05": "duplicate", "B-06": "duplicate", "B-07": "duplicate", "B-08": "duplicate",
  "B-09": "duplicate", "B-10": "distinct", "B-11": "distinct", "B-12": "ambiguous-owner",
  "B-13": "ambiguous-owner", "B-14": "ambiguous-owner", "B-15": "ambiguous-owner",
  "B-16": "duplicate", "B-17": "duplicate", "B-18": "duplicate", "B-19": "duplicate",
  "B-20": "ambiguous-owner", "B-21": "distinct", "B-22": "duplicate", "B-23": "duplicate",
  "B-24": "duplicate", "B-25": "duplicate", "B-26": "duplicate", "B-27": "duplicate",
  "B-28": "duplicate", "B-29": "duplicate", "B-30": "duplicate", "B-31": "duplicate",
  "B-32": "duplicate", "B-33": "ambiguous-owner", "B-34": "duplicate", "B-35": "duplicate",
  "B-36": "ambiguous-owner", "B-37": "ambiguous-owner", "B-38": "ambiguous-owner",
  "B-39": "ambiguous-owner", "B-40": "duplicate", "B-41": "duplicate", "B-42": "duplicate",
  "B-43": "duplicate", "B-44": "duplicate", "B-45": "duplicate", "B-46": "duplicate",
  "B-47": "duplicate", "B-48": "duplicate", "B-49": "duplicate", "B-50": "duplicate",
  "B-51": "duplicate", "B-52": "duplicate", "B-53": "duplicate", "B-54": "duplicate",
  "B-55": "duplicate", "B-56": "distinct", "B-57": "duplicate", "B-58": "duplicate",
  "B-59": "duplicate", "B-60": "duplicate",
  "C-01": "ambiguous-owner", "C-02": "duplicate", "C-03": "duplicate", "C-04": "distinct",
  "C-05": "distinct", "C-06": "distinct", "C-07": "distinct", "C-08": "distinct",
  "C-09": "duplicate", "C-10": "distinct", "C-11": "distinct", "C-12": "distinct",
  "C-13": "distinct", "C-14": "distinct", "C-15": "distinct", "C-16": "distinct",
  "C-17": "distinct", "C-18": "duplicate", "C-19": "distinct", "C-20": "distinct",
  "C-21": "distinct", "C-22": "duplicate", "C-23": "ambiguous-owner",
  "C-24": "ambiguous-owner", "C-25": "distinct", "C-26": "distinct",
  "D-01": "ambiguous-owner", "D-02": "distinct", "D-03": "distinct", "D-04": "distinct",
  "D-05": "distinct", "D-06": "distinct", "D-07": "distinct", "D-08": "distinct",
  "D-09": "distinct", "D-10": "duplicate", "D-11": "duplicate", "D-12": "distinct",
  "D-13": "distinct", "D-14": "distinct", "D-15": "distinct", "D-16": "distinct",
  "D-17": "distinct", "D-18": "distinct", "D-19": "distinct", "D-20": "distinct",
  "D-21": "distinct", "D-22": "distinct", "D-23": "distinct", "D-24": "distinct",
  "D-25": "distinct", "D-26": "distinct", "D-27": "distinct", "D-28": "distinct",
  "D-29": "distinct", "D-30": "distinct", "D-31": "distinct", "D-32": "distinct",
  "D-33": "distinct", "D-34": "distinct", "D-35": "distinct", "D-36": "distinct",
  "D-37": "distinct", "D-38": "distinct", "D-39": "distinct", "D-40": "distinct",
  "D-41": "distinct", "D-42": "distinct", "D-43": "distinct", "D-44": "distinct",
  "D-45": "ambiguous-owner", "D-46": "distinct", "D-47": "distinct", "D-48": "distinct",
  "D-49": "distinct", "D-50": "distinct", "D-51": "distinct", "D-52": "distinct",
  "D-53": "distinct", "D-54": "distinct", "D-55": "distinct", "D-56": "distinct",
  "D-57": "distinct", "D-58": "duplicate", "D-59": "distinct", "D-60": "distinct",
  "E-01": "duplicate", "E-02": "duplicate", "E-03": "distinct", "E-04": "distinct",
  "E-05": "distinct", "E-06": "ambiguous-owner", "E-07": "distinct", "E-08": "distinct",
  "E-09": "distinct", "E-10": "distinct", "E-11": "distinct", "E-12": "distinct",
  "E-13": "distinct", "E-14": "distinct", "E-15": "distinct", "E-16": "distinct",
  "E-17": "distinct", "E-18": "distinct", "E-19": "distinct", "E-20": "distinct",
  "E-21": "distinct", "E-22": "distinct", "E-23": "distinct", "E-24": "distinct",
  "E-25": "distinct", "E-26": "distinct", "E-27": "distinct",
  "F-01": "duplicate", "F-02": "duplicate", "F-03": "duplicate", "F-04": "duplicate",
  "F-05": "duplicate", "F-06": "distinct", "F-07": "distinct", "F-08": "duplicate",
  "F-09": "duplicate", "F-10": "duplicate", "F-11": "duplicate", "F-12": "duplicate",
  "F-13": "duplicate", "F-14": "distinct", "F-15": "duplicate", "F-16": "duplicate",
  "F-17": "duplicate", "F-18": "duplicate", "F-19": "duplicate", "F-20": "duplicate",
  "F-21": "duplicate", "F-22": "duplicate", "F-23": "duplicate", "F-24": "duplicate",
  "F-25": "duplicate", "F-26": "duplicate", "F-27": "duplicate", "F-28": "ambiguous-owner",
  "F-29": "distinct", "F-30": "duplicate", "F-31": "duplicate", "F-32": "distinct",
  "F-33": "duplicate", "F-34": "duplicate", "F-35": "distinct", "F-36": "distinct",
  "F-37": "distinct", "F-38": "duplicate", "F-39": "duplicate", "F-40": "duplicate",
  "F-41": "ambiguous-owner", "F-42": "ambiguous-owner",
  "M-01": "distinct", "M-02": "distinct", "M-03": "distinct", "M-04": "distinct",
  "M-05": "distinct", "M-06": "distinct", "M-07": "distinct"
}
```

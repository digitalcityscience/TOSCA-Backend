from django.db import migrations


PH = "public_health"

SEED = [
    {
        "code": "field_of_action",
        "label": "Field of Action",
        "selection_mode": "multiple",
        "terms": [
            ("begegnung_austausch", "Meeting and Exchange"),
            ("beratung_familienhilfe", "Counseling and Family Support"),
            ("erholung_entspannung", "Recreation, Relaxation, and Stress Management"),
            ("ernaehrung_kochen", "Nutrition and Cooking"),
            ("kultur_freizeit", "Culture and Leisure"),
            ("nachbarschaftshilfe_ehrenamt", "Neighborhood Support and Volunteering"),
            ("selbsthilfegruppen", "Self-Help Groups"),
            ("sport_bewegung", "Sports and Physical Activity"),
            ("digital_kompetenz", "Using Computers, Tablets, and Smartphones"),
            ("spielen", "Play"),
            ("gewalt_mobbing_extremismus", "Sexualized Violence, Bullying, and Extremism"),
            ("sexualitaet", "Sexuality"),
            ("sucht", "Addiction"),
            ("mentale_gesundheit", "Mental Health"),
        ],
    },
    {
        "code": "format_ph",
        "label": "Offer Type",
        "selection_mode": "single",
        "terms": [
            ("sprechstunde", "Consultation Hours"),
            ("kurs", "Course"),
            ("workshop", "Workshop"),
            ("infoveranstaltung", "Information Event"),
            ("seminar", "Seminar"),
            ("fortbildung", "Training"),
            ("beratung", "Counseling"),
            ("pflege", "Care"),
            ("vortrag", "Lecture"),
            ("treff", "Meetup"),
            ("vorsorgetermin", "Preventive Care Appointment"),
            ("tagesstrukturierend", "Day-Structuring Offer"),
            ("unterstuetztes_wohnen", "Supported Living"),
            ("besondere_wohnform", "Special Housing Form"),
            ("selbsthilfegruppe", "Self-Help Group"),
        ],
    },
    {
        "code": "organization_type",
        "label": "Organization Type",
        "selection_mode": "single",
        "terms": [
            {
                "code": "bildungseinrichtung",
                "label": "Educational Institution",
                "children": [
                    ("kindergarten_hort", "Kindergarten or After-School Care"),
                    ("grundschule", "Primary School"),
                    ("weiterfuehrende_schule", "Secondary School"),
                    ("hochschule", "University"),
                    ("volkshochschule", "Adult Education Center"),
                ],
            },
            {
                "code": "gesundheitsdienstleister",
                "label": "Health Service Provider",
                "children": [
                    ("krankenhaus", "Hospital"),
                    ("gesundheitszentrum", "Health Center"),
                    ("arztpraxis", "Medical Practice"),
                    ("fachklinik", "Specialist Clinic"),
                    ("rehaklinik", "Rehabilitation Clinic"),
                    ("apotheke", "Pharmacy"),
                    ("psychotherapeutische_praxis", "Psychotherapy Practice"),
                    ("ergotherapie", "Occupational Therapy"),
                    ("logotherapie", "Speech Therapy"),
                    ("physiotherapie", "Physiotherapy"),
                ],
            },
            {
                "code": "privater_leistungserbringer",
                "label": "Private Service Provider",
                "children": [
                    ("sportstudio", "Fitness Studio"),
                    ("heilpraktiker", "Alternative Practitioner"),
                ],
            },
            {
                "code": "zivilgesellschaft",
                "label": "Civil Society Organization or Association",
                "children": [
                    ("selbsthilfegruppe_zivil", "Self-Help Group"),
                    ("verein", "Association"),
                    ("nachbarschaftstreff", "Neighborhood Meeting Place"),
                ],
            },
            {"code": "krankenkasse", "label": "Health Insurance Fund"},
            {"code": "gemeinschaftsstaette", "label": "Community or Meeting Center"},
            {"code": "kultureinrichtung", "label": "Cultural Institution"},
            {"code": "kommune", "label": "Municipality"},
            {"code": "quartiersmanagement", "label": "Neighborhood Management"},
            {"code": "gesundheitsamt", "label": "Public Health Department"},
            {"code": "sozialpsych_dienst", "label": "Social Psychiatric Service"},
            {"code": "beratungseinrichtung", "label": "Counseling Center"},
            {"code": "religionsgemeinschaft", "label": "Religious or Faith Community"},
            {"code": "kinder_jugendarbeit", "label": "Child and Youth Work"},
            {"code": "inklusionsbetrieb", "label": "Inclusive Enterprise"},
            {"code": "werkstatt_behinderung", "label": "Workshop for People with Disabilities"},
            {"code": "gesundheitstreff", "label": "Health Meeting Place"},
            {"code": "servicezentrum", "label": "Service Center"},
            {"code": "freizeiteinrichtung", "label": "Leisure Facility"},
            {
                "code": "wohlfahrtsverband",
                "label": "Welfare Association",
                "children": [
                    ("awo", "AWO"),
                    ("caritas", "Caritas"),
                    ("diakonie", "Diakonie"),
                    ("drk", "DRK"),
                    ("paritaetisch", "Der Paritaetische"),
                ],
            },
            {"code": "stiftung", "label": "Foundation"},
            {
                "code": "stadtentwicklung_wohnungsbau",
                "label": "Urban Development or Housing Association",
            },
        ],
    },
    {
        "code": "age_group",
        "label": "Age Group",
        "selection_mode": "multiple",
        "terms": [
            ("early_childhood_0_6", "Early Childhood (0-6)"),
            ("early_childhood_0_3", "Early Childhood (0-3)"),
            ("kita_3_6", "Daycare Age or Phase (3-6)"),
            ("youth_10_18", "Youth (10-18)"),
            ("young_adult_18_25", "Young Adults (18-25)"),
            ("adult", "Adults"),
            ("senior", "Seniors"),
        ],
    },
    {
        "code": "audience_spec",
        "label": "Audience Specification",
        "selection_mode": "multiple",
        "terms": [
            ("fachpersonen", "Interested Professionals from the Field or Target Group"),
            ("kognitive_behinderung", "People with Cognitive Disabilities"),
            ("koerperliche_behinderung", "People with Physical Disabilities"),
            ("psychische_erkrankung", "People with Mental Illness or Psychological Disabilities"),
            ("riskanter_konsum", "People with Risky Consumption Behavior"),
            ("sinnesbehinderung", "People with Sensory Disabilities"),
            ("suchterkrankung", "People with Addiction Disorders"),
            ("demenz_kognitiv", "People with Dementia or Cognitive Impairments"),
            ("koerperliche_beeintraechtigung", "People with Physical Impairments"),
            ("psychische_beeintraechtigung", "People with Psychological or Emotional Impairments"),
            ("pflegende_angehoerige", "Family Caregivers or Comparable Close Contacts"),
            ("behinderung_allgemein", "People with Disabilities"),
            ("familien", "Families"),
            ("schwangere", "Pregnant People"),
            ("eltern", "Parents"),
            ("muetter", "Mothers"),
            ("vaeter", "Fathers"),
            ("migrationsgeschichte", "People with a Migration History"),
            ("lgbtqia", "LGBTQIA+"),
            ("maennlich", "Male"),
            ("weiblich", "Female"),
            ("divers", "Diverse"),
            ("ohne_krankenversicherung", "People without Health Insurance"),
        ],
    },
    {
        "code": "cost_category",
        "label": "Cost and Funding",
        "selection_mode": "multiple",
        "terms": [
            ("praeventionskurs_foerderfaehig", "Health-Insurance-Funded Prevention Course"),
            ("zuschuss_krankenkasse", "Health Insurance Subsidy"),
            ("krankenkassenleistung", "Health Insurance Benefit"),
            ("kostenpflichtig", "Fee-Based"),
            ("ermaessigung", "Discount Available"),
            ("spende", "Donation-Based"),
            ("but_verguenstigung", "Discount for Children Eligible for Education Benefits"),
            ("kostenlos", "Free of Charge"),
            ("sonstiger_zuschuss", "Other Subsidy Available"),
        ],
    },
    {
        "code": "accessibility",
        "label": "Accessibility",
        "selection_mode": "multiple",
        "terms": [
            ("wc_barrierefrei", "Accessible Toilet"),
            ("sprachunterstuetzung", "Language Support"),
            ("aufzuege", "Elevators Available"),
            ("aufzuege_elektrorollstuhl", "Elevators Suitable for Electric Wheelchairs"),
            ("behindertenparkplaetze", "Accessible Parking Spaces"),
            ("rollstuhlgerecht", "Wheelchair Accessible"),
            ("leichte_sprache", "Plain Language"),
            ("zugang_schwellenfrei", "Step-Free Access or Ramp"),
            ("gebaerdendolmetscher", "Sign Language Interpreter"),
            ("induktionsschleife", "Induction Loop or FM System for Hard-of-Hearing People"),
            ("reizarm_ruheraum", "Low-Stimulus Environment, Quiet Room, or Ample Space"),
            ("taktiles_leitsystem", "Tactile Guidance System for Blind People"),
            ("assistenz_erlaubt", "Assistance or Companion Allowed"),
        ],
    },
    {
        "code": "further_info",
        "label": "Further Information",
        "selection_mode": "multiple",
        "terms": [
            ("oepnv_erreichbar", "Easy to Reach by Public Transport"),
            ("kinderbetreuung_vor_ort", "Childcare Available On Site"),
            ("parkplaetze", "Parking Spaces Available"),
            ("bring_abholservice", "Drop-Off and Pick-Up Service"),
            ("fahrradabstellanlage", "Bicycle Parking Available"),
            ("dusch_waschraum", "Shower and Washing Room Available"),
            ("umkleideraeume", "Changing Rooms Available"),
            ("raeume_klimatisiert", "Air-Conditioned Rooms"),
            ("anonym", "Anonymous"),
        ],
    },
]


def _seed_term(TaxonomyTerm, dimension, term_data, sort_order, parent=None):
    if isinstance(term_data, tuple):
        code, label = term_data
        children = []
    else:
        code = term_data["code"]
        label = term_data["label"]
        children = term_data.get("children", [])

    term, _ = TaxonomyTerm.objects.update_or_create(
        dimension=dimension,
        code=code,
        defaults={
            "parent": parent,
            "label": label,
            "sort_order": sort_order,
            "is_active": True,
        },
    )

    for child_sort_order, child_data in enumerate(children, start=1):
        _seed_term(TaxonomyTerm, dimension, child_data, child_sort_order, parent=term)

    return term


def seed_ph_taxonomy(apps, schema_editor):
    TaxonomyDimension = apps.get_model("events", "TaxonomyDimension")
    TaxonomyTerm = apps.get_model("events", "TaxonomyTerm")

    for sort_order, entry in enumerate(SEED, start=1):
        dimension, _ = TaxonomyDimension.objects.update_or_create(
            code=entry["code"],
            defaults={
                "label": entry["label"],
                "selection_mode": entry["selection_mode"],
                "profile_key": PH,
                "sort_order": sort_order,
                "is_active": True,
            },
        )
        for term_sort_order, term_data in enumerate(entry["terms"], start=1):
            _seed_term(TaxonomyTerm, dimension, term_data, term_sort_order)


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0017_publichealtheventprofile_cost_amount_eur_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_ph_taxonomy, migrations.RunPython.noop),
    ]

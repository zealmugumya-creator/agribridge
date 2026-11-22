-- =============================================================
-- Phase 5: Agricultural Terminology Database
-- Run this in the Supabase SQL Editor for project lvloaweijidgcuezdnmu
-- =============================================================

-- Enable pg_trgm for fuzzy search (safe to run even if already enabled)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS agri_dictionary (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  term_key    text NOT NULL,           -- machine key e.g. "maize"
  category    text NOT NULL,           -- e.g. "cereal_crops", "livestock", "tools"
  en          text NOT NULL,           -- English (canonical)
  lg          text,                    -- Luganda
  rn          text,                    -- Runyankole
  rk          text,                    -- Rukiga
  ls          text,                    -- Lusoga
  ac          text,                    -- Acholi
  sw          text,                    -- Kiswahili
  rw          text,                    -- Kinyarwanda
  fr          text,                    -- French
  ar          text,                    -- Arabic
  notes       text,                    -- translator notes / disambiguation
  created_at  timestamptz DEFAULT now(),
  updated_at  timestamptz DEFAULT now(),
  UNIQUE(term_key)
);

-- RLS
ALTER TABLE agri_dictionary ENABLE ROW LEVEL SECURITY;

CREATE POLICY "agri_dict_public_read" ON agri_dictionary
  FOR SELECT USING (true);

CREATE POLICY "agri_dict_admin_write" ON agri_dictionary
  FOR ALL USING (
    (auth.jwt() ->> 'role') = 'admin' OR
    auth.role() = 'service_role'
  );

-- Indexes
CREATE INDEX IF NOT EXISTS agri_dict_category_idx ON agri_dictionary(category);
CREATE INDEX IF NOT EXISTS agri_dict_term_key_idx  ON agri_dictionary(term_key);
CREATE INDEX IF NOT EXISTS agri_dict_en_trgm_idx   ON agri_dictionary USING gin(en gin_trgm_ops);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION agri_dict_set_updated()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

DROP TRIGGER IF EXISTS agri_dict_updated ON agri_dictionary;
CREATE TRIGGER agri_dict_updated
  BEFORE UPDATE ON agri_dictionary
  FOR EACH ROW EXECUTE FUNCTION agri_dict_set_updated();

-- =============================================================
-- Seed data: 25 categories × representative terms
-- =============================================================
INSERT INTO agri_dictionary (term_key, category, en, lg, rn, rk, ls, ac, sw, rw, fr, ar) VALUES

-- CEREAL CROPS
('maize',        'cereal_crops', 'Maize / Corn',      'Kasoli',      'Oburo',       'Oburo',       'Obuule',    'Kal',       'Mahindi',     'Ibigori',    'Maïs',          'الذرة'),
('sorghum',      'cereal_crops', 'Sorghum',           'Omuwemba',    'Omushama',    'Omushama',    'Omwemba',   'Kal maleng','Mtama',       'Amasaka',    'Sorgho',        'الذرة الرفيعة'),
('millet',       'cereal_crops', 'Finger Millet',     'Obulo',       'Obulo',       'Obulo',       'Obulo',     'Kal ngec',  'Wimbi',       'Uburo',      'Mil eleusine',  'الدخن'),
('wheat',        'cereal_crops', 'Wheat',             'Ngano',       'Ngano',       'Ngano',       'Ngano',     'Wit',       'Ngano',       'Ingano',     'Blé',           'قمح'),
('rice',         'cereal_crops', 'Rice',              'Omuceere',    'Omucere',     'Omucere',     'Omuceere',  'Mucere',    'Mchele',      'Umuceli',    'Riz',           'أرز'),

-- ROOT & TUBER CROPS
('cassava',      'root_tubers',  'Cassava',           'Muwogo',      'Obwire',      'Obwire',      'Muwogo',    'Kwon',      'Muhogo',      'Imyumbati',  'Manioc',        'الكاسافا'),
('sweet_potato', 'root_tubers',  'Sweet Potato',      'Lumonde',     'Obutakuri',   'Obutakuri',   'Lumonde',   'Acaa',      'Viazi vitamu','Ibijumba',   'Patate douce',  'البطاطا الحلوة'),
('potato',       'root_tubers',  'Irish Potato',      'Lumonde lyeru','Obwire',     'Obwire',      'Lumonde',   'Patate',    'Viazi',       'Ibirayi',    'Pomme de terre','البطاطس'),
('yam',          'root_tubers',  'Yam',               'Kaloli',      'Obugari',     'Obugari',     'Kaloli',    'Anyara',    'Mhindi',      'Urutoki',    'Igname',        'اليام'),
('arrow_root',   'root_tubers',  'Arrow Root',        'Omusooli',    'Omusooli',    'Omusooli',    'Omusooli',  'Omusooli',  'Nduma',       'Icyomyo',    'Marante',       'جذر السهم'),

-- LEGUMES
('beans',        'legumes',      'Beans',             'Ebijanjaalo', 'Ebijanjaalo', 'Ebijanjaalo', 'Ebijanjaalo','Labola',   'Maharagwe',   'Ibishyimbo', 'Haricots',      'الفاصولياء'),
('groundnuts',   'legumes',      'Groundnuts / Peanuts','Ebinyeebwa','Ebinyeebwa',  'Ebinyeebwa',  'Ebinyeebwa','Ngor',      'Karanga',     'Amasaka',    'Arachides',     'الفول السوداني'),
('soybeans',     'legumes',      'Soybeans',          'Soya',        'Soya',        'Soya',        'Soya',      'Soya',      'Soya',        'Isoya',      'Soja',          'فول الصويا'),
('cowpeas',      'legumes',      'Cowpeas',           'Engirigiri',  'Oburo',       'Oburo',       'Engirigiri','Bonyo',     'Kunde',       'Ibiharage',  'Niébé',         'اللوبيا'),
('pigeon_peas',  'legumes',      'Pigeon Peas',       'Empindi',     'Empindi',     'Empindi',     'Empindi',   'Mpindi',    'Mbaazi',      'Amashaza',   'Pois cajan',    'حمص الحمام'),

-- VEGETABLES
('cabbage',      'vegetables',   'Cabbage',           'Kabeeji',     'Kabeeji',     'Kabeeji',     'Kabeeji',   'Kabeeji',   'Kabichi',     'Kaboji',     'Chou',          'الكرنب'),
('tomato',       'vegetables',   'Tomato',            'Nyanya',      'Nyanya',      'Nyanya',      'Nyanya',    'Nyanya',    'Nyanya',      'Inyanya',    'Tomate',        'الطماطم'),
('onion',        'vegetables',   'Onion',             'Akatungulu',  'Obutunguru',  'Obutunguru',  'Akatungulu','Anyena',    'Kitunguu',    'Igitunguru', 'Oignon',        'البصل'),
('eggplant',     'vegetables',   'Eggplant / Brinjal','Biringanya',  'Biringanya',  'Biringanya',  'Biringanya','Biringanya','Bilinganya',  'Ibiringanya','Aubergine',     'الباذنجان'),
('spinach',      'vegetables',   'Spinach / Sukuma',  'Dodo',        'Omusubati',   'Omusubati',   'Dodo',      'Dodo',      'Sukuma wiki', 'Isogo',      'Épinard',       'السبانخ'),
('kale',         'vegetables',   'Kale',              'Dodo',        'Omusubati',   'Omusubati',   'Dodo',      'Dodo',      'Sukuma wiki', 'Isogo',      'Chou frisé',    'الكرنب الأخضر'),
('carrot',       'vegetables',   'Carrot',            'Karoti',      'Karoti',      'Karoti',      'Karoti',    'Karoti',    'Karoti',      'Karoti',     'Carotte',       'جزرة'),
('pepper',       'vegetables',   'Bell Pepper / Capsicum','Pilipili hoho','Pilipili hoho','Pilipili hoho','Pilipili hoho','Pilipili hoho','Pilipili hoho','Poivre doux', 'الفلفل'),
('garlic',       'vegetables',   'Garlic',            'Kasubi',      'Kasubi',      'Kasubi',      'Kasubi',    'Kasubi',    'Kitunguu saumu','Icyayi',   'Ail',           'ثوم'),
('cucumber',     'vegetables',   'Cucumber',          'Kyakuyayiro', 'Kyakuyayiro', 'Kyakuyayiro', 'Kyakuyayiro','Tango',   'Tango',       'Inkonjori',  'Concombre',     'خيار'),

-- FRUITS
('banana',       'fruits',       'Banana (Matoke)',   'Omutooke',    'Omutooke',    'Omutooke',    'Omutooke',  'Tooke',     'Ndizi / Matoke','Igitoke',  'Banane plantain','الموز'),
('mango',        'fruits',       'Mango',             'Muyembe',     'Omuyembe',    'Omuyembe',    'Muyembe',   'Muyembe',   'Embe',        'Umuyembe',   'Mangue',        'المانجو'),
('avocado',      'fruits',       'Avocado',           'Ovokado',     'Ovokado',     'Ovokado',     'Ovokado',   'Ovokado',   'Parachichi',  'Avoka',      'Avocat',        'الأفوكادو'),
('passion_fruit','fruits',       'Passion Fruit',     'Pasuni',      'Pasuni',      'Pasuni',      'Pasuni',    'Pasuni',    'Pasheeni',    'Marakasuye', 'Fruit de la passion','فاكهة الباشون'),
('pineapple',    'fruits',       'Pineapple',         'Nanasi',      'Nanasi',      'Nanasi',      'Nanasi',    'Nanasi',    'Nanasi',      'Inanasi',    'Ananas',        'الأناناس'),
('watermelon',   'fruits',       'Watermelon',        'Kalambula',   'Obusheke',    'Obusheke',    'Kalambula', 'Obusheke',  'Tikiti maji', 'Inkeri',     'Pastèque',      'البطيخ'),
('jackfruit',    'fruits',       'Jackfruit',         'Fenne',       'Fenesi',      'Fenesi',      'Fenne',     'Fenne',     'Fenesi',      'Ifenesi',    'Jaque',         'جاك فروت'),
('papaya',       'fruits',       'Papaya',            'Papaali',     'Omupapaali',  'Omupapaali',  'Papaali',   'Papaali',   'Papai',       'Ipapai',     'Papaye',        'البابايا'),

-- LIVESTOCK
('cattle',       'livestock',    'Cattle / Cows',     'Ente',        'Ente',        'Ente',        'Ente',      'Dyel',      'Ng\'ombe',    'Inka',       'Bétail',        'الماشية'),
('goat',         'livestock',    'Goat',              'Embuzi',      'Embuzi',      'Embuzi',      'Embuzi',    'Dyegi',     'Mbuzi',       'Ihene',      'Chèvre',        'الماعز'),
('pig',          'livestock',    'Pig',               'Embizzi',     'Embizzi',     'Embizzi',     'Embizzi',   'Lyel',      'Nguruwe',     'Ingurube',   'Porc',          'الخنزير'),
('chicken',      'livestock',    'Chicken / Poultry', 'Enkoko',      'Enkoko',      'Enkoko',      'Enkoko',    'Dyak',      'Kuku',        'Inkoko',     'Poulet',        'الدجاج'),
('sheep',        'livestock',    'Sheep',             'Endiga',      'Endiga',      'Endiga',      'Endiga',    'Yamo',      'Kondoo',      'Intama',     'Mouton',        'الغنم'),
('rabbit',       'livestock',    'Rabbit',            'Enkwanzi',    'Enkwanzi',    'Enkwanzi',    'Enkwanzi',  'Lacuc',     'Sungura',     'Urukwavu',   'Lapin',         'الأرنب'),
('turkey',       'livestock',    'Turkey',            'Embata',      'Embata',      'Embata',      'Embata',    'Lacuc rac', 'Bata mzinga', 'Inkoko ndangwe','Dindon',     'الديك الرومي'),
('duck',         'livestock',    'Duck',              'Embata',      'Embata',      'Embata',      'Embata',    'Dek',       'Bata',        'Inkoko y''amazi','Canard',     'البطة'),

-- FISH
('tilapia',      'fish',         'Tilapia',           'Ngege',       'Engege',      'Engege',      'Ngege',     'Ngege',     'Sato / Perege','Inzige',    'Tilapia',       'تيلابيا'),
('catfish',      'fish',         'Catfish (Mudfish)',  'Semutundu',   'Omukizi',     'Omukizi',     'Semutundu', 'Olwiri',    'Kambare',     'Inzoga',     'Poisson-chat',  'السمك القطي'),
('nile_perch',   'fish',         'Nile Perch (Mputa)','Empuuta',     'Omukene',     'Omukene',     'Empuuta',   'Raa',       'Sangara',     'Inzobe',     'Perche du Nil', 'سمك النيل'),

-- FARM TOOLS
('hoe',          'farm_tools',   'Hoe',               'Enkumbi',     'Enkumbi',     'Enkumbi',     'Enkumbi',   'Kom',       'Jembe',       'Isuka',      'Houe',          'المعزقة'),
('panga',        'farm_tools',   'Panga / Machete',   'Empanga',     'Empanga',     'Empanga',     'Empanga',   'Lare',      'Panga',       'Icyuma',     'Machette',      'المنجل'),
('rake',         'farm_tools',   'Rake',              'Leeki',       'Leeki',       'Leeki',       'Leeki',     'Reki',      'Reki',        'Urugano',    'Râteau',        'مشط التربة'),
('watering_can', 'farm_tools',   'Watering Can',      'Enkopola',    'Enkopola',    'Enkopola',    'Enkopola',  'Enkopola',  'Bomba la maji','Icupa',     'Arrosoir',      'رشاشة الري'),
('wheelbarrow',  'farm_tools',   'Wheelbarrow',       'Olusero',     'Omusero',     'Omusero',     'Olusero',   'Olusero',   'Toroli',      'Icyogeli',   'Brouette',      'عربة اليد'),
('tractor',      'farm_tools',   'Tractor',           'Omugulansi',  'Trekita',     'Trekita',     'Trekita',   'Terakita',  'Trekta',      'Sitima',     'Tracteur',      'الجرار'),
('sprayer',      'farm_tools',   'Sprayer (Pump)',     'Bomba',       'Bomba',       'Bomba',       'Bomba',     'Pompo',     'Bomba',       'Pompe',      'Pulvérisateur', 'رشاش المبيدات'),
('plough',       'farm_tools',   'Plough',            'Omugulansi',  'Omugola',     'Omugola',     'Omugola',   'Palo',      'Jembe la ng''ombe','Igiti', 'Charrue',       'المحراث'),

-- FARMING PRACTICES
('irrigation',   'practices',    'Irrigation',        'Okutuuza',    'Okuteerera',  'Okuteerera',  'Okutuuza',  'Moo pi',    'Umwagiliaji', 'Kuhira',     'Irrigation',    'الري'),
('composting',   'practices',    'Composting',        'Okulundira',  'Okulundira',  'Okulundira',  'Okulundira','Compost',   'Mboji',       'Ibinyabutaka','Compostage',   'سماد الكمبوست'),
('mulching',     'practices',    'Mulching',          'Okubikka',    'Okubikka',    'Okubikka',    'Okubikka',  'Mulch',     'Kuweka matandazo','Gukubika','Paillage',    'التغطية بالمهاد'),
('intercropping','practices',    'Intercropping',     'Okusangawo',  'Okusangira',  'Okusangira',  'Okusangawo','Interrop',  'Mchanganyiko wa mazao','Kubyara hamwe','Culture intercalaire','الزراعة المختلطة'),
('crop_rotation','practices',    'Crop Rotation',     'Okyuusa ebirime','Okyuusa',   'Okyuusa',     'Okyuusa',   'Rotation',  'Mzunguko wa mazao','Guhindura inzego','Rotation des cultures','تناوب المحاصيل'),
('weeding',      'practices',    'Weeding',           'Okukola',     'Okukola',     'Okukola',     'Okukola',   'Lwit yat',  'Kupalilia',   'Gukuraho ibyatsi','Désherbage', 'نزع الأعشاب'),
('harvesting',   'practices',    'Harvesting',        'Okukungula',  'Okukungula',  'Okukungula',  'Okukungula','Kayo',      'Kuvuna',      'Gusarura',   'Récolte',       'الحصاد'),
('planting',     'practices',    'Planting / Sowing', 'Okusimba',    'Okubima',     'Okubima',     'Okusimba',  'Puro',      'Kupanda',     'Gutera',     'Plantation',    'الزراعة'),

-- SOIL & LAND
('topsoil',      'soil',         'Topsoil',           'Obutaka obunnafu','Obutaka',  'Obutaka',     'Obutaka',   'Lao',       'Udongo wa juu','Ubutaka',   'Couche arable', 'التربة السطحية'),
('fertilizer',   'soil',         'Fertilizer',        'Mbolea',      'Mbolea',      'Mbolea',      'Mbolea',    'Mbolea',    'Mbolea',      'Ifumbire',   'Engrais',       'السماد'),
('pesticide',    'soil',         'Pesticide',         'Eddagala ly''ebirowoozo','Eddagala','Eddagala','Eddagala','Yat kuyo','Dawa ya wadudu','Umuti w''inzoka','Pesticide','المبيد الحشري'),
('herbicide',    'soil',         'Herbicide',         'Eddagala ly''ebirime','Eddagala','Eddagala','Eddagala','Herbisaidi','Dawa ya magugu','Umuti w''ibyatsi','Herbicide','مبيد الأعشاب'),
('compost',      'soil',         'Compost / Manure',  'Emisinde',    'Emisinde',    'Emisinde',    'Emisinde',  'Compost',   'Mboji / Samadi','Ifumbire', 'Compost / Fumier','السماد العضوي'),

-- MARKET / TRADE
('wholesale',    'market',       'Wholesale',         'Obugula obunene','Olukiiko', 'Olukiiko',    'Olukiiko',  'Wholesale', 'Jumla',       'Ubucuruzi bukuru','Vente en gros','البيع بالجملة'),
('retail',       'market',       'Retail',            'Obugula obuto','Obugula',   'Obugula',     'Obugula',   'Retail',    'Rejareja',    'Ubucuruzi buke','Vente au détail','البيع بالتجزئة'),
('price_per_kg', 'market',       'Price per kilogram','Omuwendo ogw''ekilo','Omuwendo','Omuwendo', 'Omuwendo',  'Wel',       'Bei kwa kilo','Igiciro kuri kilo','Prix au kg','السعر لكل كيلو'),
('market_day',   'market',       'Market Day',        'Olunaku lw''akatale','Olutindo','Olutindo', 'Olutindo',  'Chiel',     'Siku ya soko','Italiki y''isoko','Jour de marché','يوم السوق'),
('export_grade', 'market',       'Export Grade',      'Gavufa ya kutumya','Gavufa', 'Gavufa',     'Gavufa',    'Export',    'Kiwango cha usafirishaji','Ubuziranenge','Qualité export','درجة التصدير'),

-- MEASUREMENTS
('kilogram',     'measurements', 'Kilogram (kg)',      'Ekilo',       'Ekilo',       'Ekilo',       'Ekilo',     'Kilo',      'Kilo / Kilogramu','Ikiro',  'Kilogramme',    'كيلوغرام'),
('tonne',        'measurements', 'Tonne (1000kg)',     'Tani',        'Etani',       'Etani',       'Tani',      'Tani',      'Tani',        'Itone',      'Tonne',         'طن'),
('acre',         'measurements', 'Acre',               'Ekka',        'Ekka',        'Ekka',        'Ekka',      'Ekka',      'Ekari',       'Ekari',      'Acre',          'فدان'),
('hectare',      'measurements', 'Hectare',            'Hekiteya',    'Hekiteya',    'Hekiteya',    'Hekiteya',  'Hekiteya',  'Hekta',       'Hegitare',   'Hectare',       'هكتار'),
('bag_100kg',    'measurements', 'Bag (100 kg)',       'Kisaka',      'Kisaka',      'Kisaka',      'Kisaka',    'Kisaka',    'Mfuko wa kilo 100','Isaho',  'Sac de 100kg', 'كيس 100 كغ'),

-- SEASONS
('dry_season',   'seasons',      'Dry Season',         'Ebiro by''omusana','Ebiro by''omusana','Ebiro by''omusana','Ebiro by''omusana','Oro','Kiangazi','Ururimo','Saison sèche','الموسم الجاف'),
('rainy_season', 'seasons',      'Rainy Season',       'Ebiro by''enkuba','Ebiro by''enkuba','Ebiro by''enkuba','Ebiro by''enkuba','Kot','Masika','Isezorero','Saison des pluies','موسم الأمطار'),
('planting_season','seasons',    'Planting Season',    'Ebiro by''okusimba','Ebiro','Ebiro','Ebiro','Ebiro','Msimu wa kupanda','Igihe cyo gutera','Saison de plantation','موسم الزراعة'),
('harvest_time', 'seasons',      'Harvest Time',       'Omulimu gw''okukungula','Ebiro','Ebiro','Ebiro','Ebiro','Msimu wa kuvuna','Igihe cyo gusarura','Période de récolte','وقت الحصاد'),

-- WEATHER
('rainfall',     'weather',      'Rainfall',           'Enkuba',      'Enkuba',      'Enkuba',      'Enkuba',    'Kot',       'Mvua',        'Imvura',     'Précipitations', 'هطول الأمطار'),
('drought',      'weather',      'Drought',            'Enjala',      'Omusana',     'Omusana',     'Omusana',   'Pii',       'Ukame',       'Amapfa',     'Sécheresse',    'الجفاف'),
('flooding',     'weather',      'Flooding',           'Amazzi amangi','Amazzi',     'Amazzi',      'Amazzi',    'Pii pi',    'Mafuriko',    'Umwuzure',   'Inondation',    'الفيضان'),

-- DISEASES & PESTS
('army_worm',    'pests',        'Fall Army Worm',     'Omubbi',      'Obusanzi',    'Obusanzi',    'Omubbi',    'Army worm', 'Viwavi jeshi','Inzoka y''ingabo','Légionnaire d''automne','دودة الجيش'),
('cassava_mosaic','pests',       'Cassava Mosaic Disease','Obulwadde bw''omuwogo','Obulwadde','Obulwadde','Obulwadde','Tuo muwogo','Ugonjwa wa mihogo','Indwara y''imyumbati','Mosaïque du manioc','مرض الموزاييك'),
('maize_borer',  'pests',        'Maize Stalk Borer',  'Omubbi w''obuuro','Omubbi',   'Omubbi',      'Omubbi',    'Kuot kal',  'Borer wa mahindi','Inzoka y''ibigori','Foreur des tiges','حفار ساق الذرة'),

-- STORAGE
('grain_store',  'storage',      'Grain Store / Granary','Entuumu',   'Entuumu',     'Entuumu',     'Entuumu',   'Kuundu',    'Ghala la nafaka','Inyumba y''ibirimba','Grenier','مخزن الحبوب'),
('cold_storage', 'storage',      'Cold Storage',       'Entuumu y''obutiti','Entuumu','Entuumu',    'Entuumu',   'Cold storage','Ghala baridi','Inyumba ikonje','Chambre froide','التخزين البارد'),
('shelf_life',   'storage',      'Shelf Life',         'Ebiro by''okubikka','Ebiro','Ebiro','Ebiro','Ebiro',      'Muda wa kuhifadhi','Igihe cyo kubika','Durée de conservation','مدة الصلاحية'),

-- ORGANIC & CERTIFICATION
('organic',      'organic',      'Organic',            'Ekibiina',    'Oruganda rw''embuto','Oruganda','Oruganda','Organic','Kilimo hai','Uburimyi buzima','Bio / Organique','عضوي'),
('certified',    'organic',      'Certified',          'Ayahamiririzibwa','Okahamiririzibwa','Okahamiririzibwa','Okahamiririzibwa','Ngeyo','Iliyoidhinishwa','Yemejwe','Certifié','معتمد'),
('fair_trade',   'organic',      'Fair Trade',         'Obusuubuzi obulongoofu','Obusuubuzi','Obusuubuzi','Obusuubuzi','Fair trade','Biashara ya haki','Ubucuruzi buringanye','Commerce équitable','التجارة العادلة')

ON CONFLICT (term_key) DO NOTHING;

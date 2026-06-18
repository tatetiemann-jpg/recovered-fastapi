-- ============================================================
-- Orchestra Demo Seed — org_id 21
-- Run once against the production DB.
-- Safe to re-run: uses DO blocks / ON CONFLICT where possible.
-- ============================================================

DO $$
DECLARE
    oid          CONSTANT INTEGER := 21;

    -- section IDs
    s_vn1  INTEGER;  s_vn2  INTEGER;  s_vla  INTEGER;
    s_vc   INTEGER;  s_db   INTEGER;
    s_fl   INTEGER;  s_ob   INTEGER;  s_cl   INTEGER;  s_bn   INTEGER;
    s_hn   INTEGER;  s_tp   INTEGER;  s_tb   INTEGER;  s_tu   INTEGER;
    s_timp INTEGER;  s_perc INTEGER;  s_hrp  INTEGER;

    -- concert IDs
    c1 INTEGER;  c2 INTEGER;  c3 INTEGER;

    -- piece IDs
    p1 INTEGER;  p2 INTEGER;  p3 INTEGER;  p4 INTEGER;  p5 INTEGER;  p6 INTEGER;

    -- member IDs (30 players)
    m1  INTEGER; m2  INTEGER; m3  INTEGER; m4  INTEGER; m5  INTEGER;
    m6  INTEGER; m7  INTEGER; m8  INTEGER; m9  INTEGER; m10 INTEGER;
    m11 INTEGER; m12 INTEGER; m13 INTEGER; m14 INTEGER; m15 INTEGER;
    m16 INTEGER; m17 INTEGER; m18 INTEGER; m19 INTEGER; m20 INTEGER;
    m21 INTEGER; m22 INTEGER; m23 INTEGER; m24 INTEGER; m25 INTEGER;
    m26 INTEGER; m27 INTEGER; m28 INTEGER; m29 INTEGER; m30 INTEGER;

    -- rehearsal IDs
    r1 INTEGER; r2 INTEGER; r3 INTEGER; r4 INTEGER; r5 INTEGER;

BEGIN

-- ── 0. Clear any existing demo data for this org ─────────────────────────
DELETE FROM orchestra_sub_contacts   WHERE sub_request_id IN (SELECT id FROM orchestra_sub_requests WHERE rehearsal_id IN (SELECT id FROM rehearsals WHERE org_id=oid));
DELETE FROM orchestra_absence_requests WHERE rehearsal_id IN (SELECT id FROM rehearsals WHERE org_id=oid);
DELETE FROM orchestra_sub_requests   WHERE rehearsal_id  IN (SELECT id FROM rehearsals WHERE org_id=oid);
DELETE FROM orchestra_rehearsal_sections WHERE rehearsal_id IN (SELECT id FROM rehearsals WHERE org_id=oid);
DELETE FROM orchestra_attendance     WHERE rehearsal_id  IN (SELECT id FROM rehearsals WHERE org_id=oid);
DELETE FROM rehearsals               WHERE org_id=oid AND rehearsal_type='orchestra';
DELETE FROM piece_seats              WHERE piece_id IN (SELECT cp.id FROM concert_pieces cp JOIN operas o ON o.id=cp.opera_id WHERE o.org_id=oid);
DELETE FROM concert_pieces           WHERE opera_id IN (SELECT id FROM operas WHERE org_id=oid);
DELETE FROM operas                   WHERE org_id=oid;
DELETE FROM orchestra_subs           WHERE org_id=oid;
DELETE FROM orchestra_members        WHERE org_id=oid;
DELETE FROM orchestra_sections       WHERE org_id=oid;


-- ── 1. Sections ──────────────────────────────────────────────────────────
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Violin I',   'violin',        1,  16) RETURNING id INTO s_vn1;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Violin II',  'violin',        2,  14) RETURNING id INTO s_vn2;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Viola',      'viola',         3,  10) RETURNING id INTO s_vla;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Cello',      'cello',         4,   8) RETURNING id INTO s_vc;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Double Bass','double bass',   5,   6) RETURNING id INTO s_db;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Flute',      'flute',         6,   3) RETURNING id INTO s_fl;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Oboe',       'oboe',          7,   2) RETURNING id INTO s_ob;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Clarinet',   'clarinet',      8,   3) RETURNING id INTO s_cl;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Bassoon',    'bassoon',       9,   2) RETURNING id INTO s_bn;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'French Horn','french horn',  10,   4) RETURNING id INTO s_hn;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Trumpet',    'trumpet',      11,   3) RETURNING id INTO s_tp;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Trombone',   'trombone',     12,   3) RETURNING id INTO s_tb;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Tuba',       'tuba',         13,   1) RETURNING id INTO s_tu;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Timpani',    'timpani',      14,   1) RETURNING id INTO s_timp;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Percussion', 'percussion',   15,   2) RETURNING id INTO s_perc;
INSERT INTO orchestra_sections (org_id, name, instrument, sort_order, chair_count)
VALUES (oid,'Harp',       'harp',         16,   1) RETURNING id INTO s_hrp;


-- ── 2. Members (30 players) ──────────────────────────────────────────────
-- Violin I (6)
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Elena Marsh','elena.marsh@email.com','violin','strings',s_vn1,'1st Violin') RETURNING id INTO m1;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'James Kwan','james.kwan@email.com','violin','strings',s_vn1,'1st Violin') RETURNING id INTO m2;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Priya Nair','priya.nair@email.com','violin','strings',s_vn1,'1st Violin') RETURNING id INTO m3;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Thomas Vega','thomas.vega@email.com','violin','strings',s_vn1,'1st Violin') RETURNING id INTO m4;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Sofia Reyes','sofia.reyes@email.com','violin','strings',s_vn1,'1st Violin') RETURNING id INTO m5;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Leo Fontaine','leo.fontaine@email.com','violin','strings',s_vn1,'1st Violin') RETURNING id INTO m6;
-- Violin II (4)
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Hannah Blake','hannah.blake@email.com','violin','strings',s_vn2,'2nd Violin') RETURNING id INTO m7;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Marcus Liu','marcus.liu@email.com','violin','strings',s_vn2,'2nd Violin') RETURNING id INTO m8;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Rachel Kim','rachel.kim@email.com','violin','strings',s_vn2,'2nd Violin') RETURNING id INTO m9;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Oliver Grant','oliver.grant@email.com','violin','strings',s_vn2,'2nd Violin') RETURNING id INTO m10;
-- Viola (3)
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Claire Dupont','claire.dupont@email.com','viola','strings',s_vla,NULL) RETURNING id INTO m11;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'David Park','david.park@email.com','viola','strings',s_vla,NULL) RETURNING id INTO m12;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Nina Torres','nina.torres@email.com','viola','strings',s_vla,NULL) RETURNING id INTO m13;
-- Cello (3)
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Samuel West','samuel.west@email.com','cello','strings',s_vc,NULL) RETURNING id INTO m14;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Amara Osei','amara.osei@email.com','cello','strings',s_vc,NULL) RETURNING id INTO m15;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Julian Cross','julian.cross@email.com','cello','strings',s_vc,NULL) RETURNING id INTO m16;
-- Double Bass (2)
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Felix Hartmann','felix.hartmann@email.com','double bass','strings',s_db,NULL) RETURNING id INTO m17;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,part_label) VALUES (oid,'Yuki Tanaka','yuki.tanaka@email.com','double bass','strings',s_db,NULL) RETURNING id INTO m18;
-- Winds (1 each + 1 extra flute)
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Maya Chen','maya.chen@email.com','flute','woodwinds',s_fl) RETURNING id INTO m19;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Liam O''Brien','liam.obrien@email.com','oboe','woodwinds',s_ob) RETURNING id INTO m20;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Gabriela Ruiz','gabriela.ruiz@email.com','clarinet','woodwinds',s_cl) RETURNING id INTO m21;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Ethan Moore','ethan.moore@email.com','bassoon','woodwinds',s_bn) RETURNING id INTO m22;
-- Brass
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Isabella Scott','isabella.scott@email.com','french horn','brass',s_hn) RETURNING id INTO m23;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Nathan Brooks','nathan.brooks@email.com','trumpet','brass',s_tp) RETURNING id INTO m24;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Zoe Adams','zoe.adams@email.com','trombone','brass',s_tb) RETURNING id INTO m25;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Derek Stone','derek.stone@email.com','tuba','brass',s_tu) RETURNING id INTO m26;
-- Percussion
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Aaliya Patel','aaliya.patel@email.com','timpani','percussion',s_timp) RETURNING id INTO m27;
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Connor Walsh','connor.walsh@email.com','percussion','percussion',s_perc) RETURNING id INTO m28;
-- Harp
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id) VALUES (oid,'Vivienne Laurent','vivienne.laurent@email.com','harp','strings',s_hrp) RETURNING id INTO m29;
-- Extra: inactive member
INSERT INTO orchestra_members (org_id,fullname,email,instrument,section_family,section_id,active) VALUES (oid,'Robert Vance','robert.vance@email.com','violin','strings',s_vn1,false) RETURNING id INTO m30;


-- ── 3. Concerts ──────────────────────────────────────────────────────────
INSERT INTO operas (org_id, opera_name, start_date, end_date)
VALUES (oid, 'Spring Gala Concert', '2026-04-12', '2026-04-12') RETURNING id INTO c1;

INSERT INTO operas (org_id, opera_name, start_date, end_date)
VALUES (oid, 'Romantic Evening', '2026-06-28', '2026-06-28') RETURNING id INTO c2;

INSERT INTO operas (org_id, opera_name, start_date, end_date)
VALUES (oid, 'Season Finale: The Planets & More', '2026-09-19', '2026-09-20') RETURNING id INTO c3;


-- ── 4. Concert Pieces ────────────────────────────────────────────────────
-- Spring Gala
INSERT INTO concert_pieces (opera_id,title,composer,opus,duration_min,sort_order)
VALUES (c1,'Symphony No. 5 in C minor','Ludwig van Beethoven','Op. 67',33,1) RETURNING id INTO p1;
INSERT INTO concert_pieces (opera_id,title,composer,opus,duration_min,sort_order)
VALUES (c1,'Overture to "A Midsummer Night''s Dream"','Felix Mendelssohn','Op. 61',13,2) RETURNING id INTO p2;

-- Romantic Evening
INSERT INTO concert_pieces (opera_id,title,composer,opus,duration_min,sort_order)
VALUES (c2,'Piano Concerto No. 2 in C minor','Sergei Rachmaninoff','Op. 18',35,1) RETURNING id INTO p3;
INSERT INTO concert_pieces (opera_id,title,composer,opus,duration_min,sort_order)
VALUES (c2,'Symphony No. 6 "Pathétique"','Pyotr Ilyich Tchaikovsky','Op. 74',47,2) RETURNING id INTO p4;

-- Season Finale
INSERT INTO concert_pieces (opera_id,title,composer,opus,duration_min,sort_order)
VALUES (c3,'The Planets Suite','Gustav Holst','Op. 32',50,1) RETURNING id INTO p5;
INSERT INTO concert_pieces (opera_id,title,composer,opus,duration_min,sort_order)
VALUES (c3,'Finlandia','Jean Sibelius','Op. 26',8,2) RETURNING id INTO p6;


-- ── 5. Piece Seats (Beethoven 5 — chair 1 of each section) ──────────────
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn1,1,1,m1);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn1,2,1,m2);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn1,3,1,m3);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn1,4,2,m4);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn1,5,2,m5);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn2,1,1,m7);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vn2,2,1,m8);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vla,1,1,m11);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vla,2,1,m12);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vc,1,1,m14);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_vc,2,1,m15);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_db,1,1,m17);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_fl,1,1,m19);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_ob,1,1,m20);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_cl,1,1,m21);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_bn,1,1,m22);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_hn,1,1,m23);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_tp,1,1,m24);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_tb,1,1,m25);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_tu,1,1,m26);
INSERT INTO piece_seats (piece_id,section_id,chair_number,part_number,member_id) VALUES (p1,s_timp,1,1,m27);


-- ── 6. Rehearsals (5 — mix of past and upcoming) ─────────────────────────
INSERT INTO rehearsals (org_id,start_time,end_time,location,notes,rehearsal_type,attendance_type,opera_id)
VALUES (oid,'2026-05-06 19:00','2026-05-06 21:30','Rehearsal Hall A',
        'Full orchestra — focus on Beethoven mvmts I & II','orchestra','full',c1)
RETURNING id INTO r1;

INSERT INTO rehearsals (org_id,start_time,end_time,location,notes,rehearsal_type,attendance_type,opera_id)
VALUES (oid,'2026-05-20 19:00','2026-05-20 21:30','Rehearsal Hall A',
        'Full orchestra — Beethoven III & IV + Mendelssohn run-through','orchestra','full',c1)
RETURNING id INTO r2;

INSERT INTO rehearsals (org_id,start_time,end_time,location,notes,rehearsal_type,attendance_type,opera_id)
VALUES (oid,'2026-06-03 19:00','2026-06-03 20:30','Rehearsal Hall B',
        'Strings sectional — Rachmaninoff passages','orchestra','sectional',c2)
RETURNING id INTO r3;

INSERT INTO rehearsals (org_id,start_time,end_time,location,notes,rehearsal_type,attendance_type,opera_id)
VALUES (oid,'2026-06-17 19:00','2026-06-17 21:30','Rehearsal Hall A',
        'Full orchestra — Tchaikovsky Pathétique','orchestra','full',c2)
RETURNING id INTO r4;

INSERT INTO rehearsals (org_id,start_time,end_time,location,notes,rehearsal_type,attendance_type,opera_id)
VALUES (oid,'2026-07-08 19:00','2026-07-08 21:30','Rehearsal Hall A',
        'Full orchestra — Holst Planets (Mars through Jupiter)','orchestra','full',c3)
RETURNING id INTO r5;

-- Attach sections to rehearsals
-- r1 & r2 & r4 & r5: full — all sections
INSERT INTO orchestra_rehearsal_sections (rehearsal_id,section_id) VALUES
  (r1,s_vn1),(r1,s_vn2),(r1,s_vla),(r1,s_vc),(r1,s_db),
  (r1,s_fl),(r1,s_ob),(r1,s_cl),(r1,s_bn),
  (r1,s_hn),(r1,s_tp),(r1,s_tb),(r1,s_tu),(r1,s_timp),(r1,s_perc),(r1,s_hrp);
INSERT INTO orchestra_rehearsal_sections (rehearsal_id,section_id) VALUES
  (r2,s_vn1),(r2,s_vn2),(r2,s_vla),(r2,s_vc),(r2,s_db),
  (r2,s_fl),(r2,s_ob),(r2,s_cl),(r2,s_bn),
  (r2,s_hn),(r2,s_tp),(r2,s_tb),(r2,s_tu),(r2,s_timp),(r2,s_perc),(r2,s_hrp);
-- r3: strings sectional only
INSERT INTO orchestra_rehearsal_sections (rehearsal_id,section_id) VALUES
  (r3,s_vn1),(r3,s_vn2),(r3,s_vla),(r3,s_vc),(r3,s_db);
INSERT INTO orchestra_rehearsal_sections (rehearsal_id,section_id) VALUES
  (r4,s_vn1),(r4,s_vn2),(r4,s_vla),(r4,s_vc),(r4,s_db),
  (r4,s_fl),(r4,s_ob),(r4,s_cl),(r4,s_bn),
  (r4,s_hn),(r4,s_tp),(r4,s_tb),(r4,s_tu),(r4,s_timp),(r4,s_perc),(r4,s_hrp);
INSERT INTO orchestra_rehearsal_sections (rehearsal_id,section_id) VALUES
  (r5,s_vn1),(r5,s_vn2),(r5,s_vla),(r5,s_vc),(r5,s_db),
  (r5,s_fl),(r5,s_ob),(r5,s_cl),(r5,s_bn),
  (r5,s_hn),(r5,s_tp),(r5,s_tb),(r5,s_tu),(r5,s_timp),(r5,s_perc),(r5,s_hrp);


-- ── 7. Attendance for past rehearsals (r1, r2) ───────────────────────────
-- r1: most attended, m6 absent
INSERT INTO orchestra_attendance (rehearsal_id,member_id,status) VALUES
  (r1,m1,'attended'),(r1,m2,'attended'),(r1,m3,'attended'),(r1,m4,'attended'),
  (r1,m5,'attended'),(r1,m6,'absent'),
  (r1,m7,'attended'),(r1,m8,'attended'),(r1,m9,'attended'),(r1,m10,'attended'),
  (r1,m11,'attended'),(r1,m12,'attended'),(r1,m13,'attended'),
  (r1,m14,'attended'),(r1,m15,'attended'),(r1,m16,'attended'),
  (r1,m17,'attended'),(r1,m18,'attended'),
  (r1,m19,'attended'),(r1,m20,'attended'),(r1,m21,'attended'),(r1,m22,'attended'),
  (r1,m23,'attended'),(r1,m24,'attended'),(r1,m25,'attended'),(r1,m26,'attended'),
  (r1,m27,'attended'),(r1,m28,'attended'),(r1,m29,'attended');

-- r2: m9 and m22 absent
INSERT INTO orchestra_attendance (rehearsal_id,member_id,status) VALUES
  (r2,m1,'attended'),(r2,m2,'attended'),(r2,m3,'attended'),(r2,m4,'attended'),
  (r2,m5,'attended'),(r2,m6,'attended'),
  (r2,m7,'attended'),(r2,m8,'attended'),(r2,m9,'absent'),(r2,m10,'attended'),
  (r2,m11,'attended'),(r2,m12,'attended'),(r2,m13,'attended'),
  (r2,m14,'attended'),(r2,m15,'attended'),(r2,m16,'attended'),
  (r2,m17,'attended'),(r2,m18,'attended'),
  (r2,m19,'attended'),(r2,m20,'attended'),(r2,m21,'attended'),(r2,m22,'absent'),
  (r2,m23,'attended'),(r2,m24,'attended'),(r2,m25,'attended'),(r2,m26,'attended'),
  (r2,m27,'attended'),(r2,m28,'attended'),(r2,m29,'attended');


-- ── 8. Subs ──────────────────────────────────────────────────────────────
-- Violin I subs
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank,notes)
VALUES (oid,s_vn1,'Patricia Müller','patricia.muller@email.com','555-0101',true,1,'Freelance, very reliable') RETURNING id INTO r1; -- reuse r1 var
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank,notes)
VALUES (oid,s_vn1,'Chen Wei','chen.wei@email.com','555-0102',true,2,'Graduate student at conservatory');
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank,notes)
VALUES (oid,s_vn1,'Aleksei Volkov','aleksei.volkov@email.com','555-0103',false,NULL,'Available weekends only');

-- Violin II subs
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank)
VALUES (oid,s_vn2,'Sandra Novak','sandra.novak@email.com','555-0201',true,1);
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank)
VALUES (oid,s_vn2,'Tom Eriksen','tom.eriksen@email.com','555-0202',false,NULL);

-- Viola subs
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank,notes)
VALUES (oid,s_vla,'Fatima Al-Rashid','fatima.alrashid@email.com','555-0301',true,1,'Principal sub');

-- Cello subs
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank)
VALUES (oid,s_vc,'Marcus Bell','marcus.bell@email.com','555-0401',true,1);

-- Flute sub
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank)
VALUES (oid,s_fl,'Rin Nakamura','rin.nakamura@email.com','555-0501',true,1);

-- Horn sub
INSERT INTO orchestra_subs (org_id,section_id,fullname,email,phone,is_preferred,preferred_rank)
VALUES (oid,s_hn,'Greg Callahan','greg.callahan@email.com','555-0601',true,1);


RAISE NOTICE 'Orchestra demo seed complete for org_id=%', oid;
END $$;

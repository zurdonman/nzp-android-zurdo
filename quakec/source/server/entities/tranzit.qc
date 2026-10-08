/*
	server/entities/tranzit.qc

	Logica exclusiva para el mapa BO2 Tranzit (Green Run):
	  - func_tranzit_bus: Autobus en movimiento por el circuito de estaciones
	  - tranzit_lamp_tp: Farolas verdes de teletransporte entre estaciones
	  - func_tranzit_director: Director de zonas y spawns por proximidad

	NOTA DE AISLAMIENTO:
	Ninguna funcion de este archivo se ejecuta en otros mapas; todas las
	entidades aqui definidas solo existen dentro de maps/tranzit.bsp y ademas
	verifican `mapname == "tranzit"`.

	Copyright (C) 2021-2026 NZ:P Team / Android Fork (zurdo)
*/

void() tranzit_bus_next;
void() tranzit_bus_wait;

// ============================================================================
// 1. AUTOBUS DE TRANZIT (func_tranzit_bus)
// ============================================================================

void() tranzit_bus_blocked =
{
	if (other.aistatus == "1") {
		// El autobus atropella a los zombies que bloqueen la carretera
		DamageHandler(other, self, 99999, DMG_TYPE_OTHER);
		return;
	}

	if (other.classname == "player") {
		// Empujar suavemente al jugador en la direccion de avance del bus
		vector nudge = normalize(self.velocity) * 12;
		nudge_z = 2;
		setorigin(other, other.origin + nudge);
		return;
	}
};

void() tranzit_bus_touch =
{
	if (other.classname != "player" || other.downed)
		return;

	// Si el autobus esta detenido en una parada, permitir adelantar la salida
	if (self.velocity == '0 0 0' && self.nextthink > self.ltime + 2.0) {
		if (Player_UseButtonPressed(other, self) && !(other.semi_actions & SEMIACTION_USE)) {
			other.semi_actions |= SEMIACTION_USE;
			Sound_PlaySound(self, "sounds/misc/ching.wav", SOUND_TYPE_ENV_OBJECT, SOUND_PRIORITY_PLAYALWAYS);
			centerprint(other, "T.E.D.D.: Departing now! Hold on!\n");
			self.think = tranzit_bus_next;
			self.nextthink = self.ltime + 0.5;
		}
	}
};

void() tranzit_bus_warn_depart =
{
	entity pl = find(world, classname, "player");
	while (pl != world) {
		if (vlen(pl.origin - (self.absmin + self.absmax) * 0.5) < 950) {
			centerprint(pl, "T.E.D.D.: Bus departing in 5 seconds!\n");
		}
		pl = find(pl, classname, "player");
	}
	Sound_PlaySound(self, "sounds/misc/ching.wav", SOUND_TYPE_ENV_OBJECT, SOUND_PRIORITY_PLAYALWAYS);
	self.think = tranzit_bus_next;
	self.nextthink = self.ltime + 5.0;
};

void() tranzit_bus_wait =
{
	self.velocity = '0 0 0';

	if (self.message != "") {
		entity pl = find(world, classname, "player");
		while (pl != world) {
			if (vlen(pl.origin - (self.absmin + self.absmax) * 0.5) < 1100) {
				centerprint(pl, strcat("T.E.D.D.: Arrived at ", strcat(self.message, "\n")));
			}
			pl = find(pl, classname, "player");
		}
	}

	if (self.wait > 6) {
		self.think = tranzit_bus_warn_depart;
		self.nextthink = self.ltime + (self.wait - 5.0);
	} else if (self.wait > 0) {
		self.think = tranzit_bus_next;
		self.nextthink = self.ltime + self.wait;
	} else {
		self.think = tranzit_bus_next;
		self.nextthink = self.ltime + 0.05;
	}
};

void() tranzit_bus_next =
{
	entity targ;

	targ = find(world, targetname, self.target);
	if (targ == world) {
		self.nextthink = self.ltime + 1.0;
		self.think = SUB_Null;
		return;
	}

	self.target = targ.target;
	self.wait = targ.wait;
	self.message = targ.message;

	if (self.wait >= 10) {
		Sound_PlaySound(self, "sounds/misc/buy.wav", SOUND_TYPE_ENV_OBJECT, SOUND_PRIORITY_PLAYALWAYS);
	}

	SUB_CalcMove(targ.origin - self.mins, self.speed, tranzit_bus_wait);
};

void() tranzit_bus_find =
{
	entity targ;

	targ = find(world, targetname, self.target);
	if (targ == world)
		return;

	self.target = targ.target;
	self.wait = targ.wait;
	self.message = targ.message;

	setorigin(self, targ.origin - self.mins);
	self.nextthink = self.ltime + 20.0;
	self.think = tranzit_bus_warn_depart;
};

void() func_tranzit_bus =
{
	if (mapname != "tranzit") {
		remove(self);
		return;
	}

	if (!self.speed)
		self.speed = 210;
	if (!self.dmg)
		self.dmg = 500;

	precache_sound("sounds/misc/buy.wav");
	precache_sound("sounds/misc/ching.wav");

	self.solid = SOLID_BSP;
	self.movetype = MOVETYPE_PUSH;
	self.blocked = tranzit_bus_blocked;
	self.touch = tranzit_bus_touch;
	self.classname = "func_tranzit_bus";

	setmodel(self, self.model);
	setsize(self, self.mins, self.maxs);
	setorigin(self, self.origin);

	self.nextthink = self.ltime + 0.2;
	self.think = tranzit_bus_find;
};

// ============================================================================
// 2. FAROLAS VERDES DE TELETRANSPORTE (tranzit_lamp_tp)
// ============================================================================

void() tranzit_lamp_touch =
{
	if (other.classname != "player" || other.downed)
		return;

	if (self.death_timer > time)
		return;

	if (!isPowerOn) {
		Player_UseprintWithWait(other, self, self.useprint_index_2, 0);
		return;
	}

	float tp_cost = 250;
	Player_UseprintWithWait(other, self, self.useprint_index_1, tp_cost);

	if (!Player_UseButtonPressed(other, self) || (other.semi_actions & SEMIACTION_USE))
		return;

	other.semi_actions |= SEMIACTION_USE;

	if (other.points < tp_cost) {
		centerprint(other, STR_NOTENOUGHPOINTS);
		Sound_PlaySound(other, "sounds/misc/denybuy.wav", SOUND_TYPE_ENV_CHING, SOUND_PRIORITY_PLAYALWAYS);
		return;
	}

	entity dest = find(world, targetname, self.target);
	if (dest == world)
		return;

	Player_RemoveScore(other, tp_cost);
	self.death_timer = time + 4.0;
	dest.death_timer = time + 3.0;

	Sound_PlaySound(other, "sounds/machines/elec_shock.wav", SOUND_TYPE_ENV_OBJECT, SOUND_PRIORITY_PLAYALWAYS);
	setorigin(other, dest.origin + '0 0 18');
	other.velocity = '0 0 0';
	other.flags = other.flags & FL_ONGROUND;

	if (dest.message != "") {
		centerprint(other, strcat("Teleported to: ", strcat(dest.message, "\n")));
	}
};

void() tranzit_lamp_tp =
{
	if (mapname != "tranzit") {
		remove(self);
		return;
	}

	precache_model("models/props/lamp_kino.mdl");
	precache_sound("sounds/machines/elec_shock.wav");
	precache_sound("sounds/misc/denybuy.wav");

	self.useprint_string_1 = "Hold %b for Green Lamp Teleporter [Cost: %c]";
	self.useprint_string_2 = "Must turn on the Power at Power Station first";
	self.useprint_index_1 = Useprint_Register(self.useprint_string_1, 0);
	self.useprint_index_2 = Useprint_Register(self.useprint_string_2, 0);

	self.solid = SOLID_TRIGGER;
	self.movetype = MOVETYPE_NONE;
	setmodel(self, "models/props/lamp_kino.mdl");
	setsize(self, '-36 -36 -36', '36 36 56');
	setorigin(self, self.origin);
	self.effects = EF_GREENLIGHT;
	self.touch = tranzit_lamp_touch;
};

// ============================================================================
// 3. DIRECTOR DE ZONAS Y SPAWNS POR PROXIMIDAD (func_tranzit_director)
// ============================================================================

string(vector pos) Tranzit_GetZoneName =
{
	// Estacion 1: Bus Depot (Sur-Oeste)
	if (pos_x < -800 && pos_y < -800)
		return "BUS DEPOT";
	// Estacion 1.5: Tunnel (Oeste)
	if (pos_x < -1400 && pos_y >= -800 && pos_y <= 900)
		return "HIGHWAY TUNNEL";
	// Estacion 2: Diner (Nor-Oeste / Norte)
	if (pos_y > 900 && pos_x < 600)
		return "DINER & GARAGE";
	// Maizal y Bunker Nacht (Centro)
	if (pos_x >= -900 && pos_x <= 900 && pos_y >= -700 && pos_y <= 900)
		return "CORNFIELD / NACHT BUNKER";
	// Estacion 3: Farm (Nor-Este)
	if (pos_x >= 600 && pos_y > 600)
		return "FARMHOUSE & BARN";
	// Estacion 4: Power Station (Este / Sur-Este)
	if (pos_x > 1100 && pos_y <= 600 && pos_y >= -1300)
		return "POWER STATION";
	// Estacion 5: Town (Sur / Sur-Este)
	if (pos_x >= -800 && pos_y < -800)
		return "TOWN (BANK & BAR)";

	return "GREEN RUN HIGHWAY";
};

float(entity sp) Tranzit_IsSpawnerNearAnyPlayer =
{
	entity pl = find(world, classname, "player");
	while (pl != world) {
		if (!pl.downed && vlen(pl.origin - sp.origin) <= 1750)
			return true;
		pl = find(pl, classname, "player");
	}
	return false;
};

void() tranzit_director_think =
{
	entity pl, sp, z;
	float changed = false;
	float active_count = 0;

	// 1. Avisar al jugador cuando entra en una nueva zona de Green Run
	pl = find(world, classname, "player");
	while (pl != world) {
		string zname = Tranzit_GetZoneName(pl.origin);
		if (pl.netname != zname) {
			pl.netname = zname;
			centerprint(pl, strcat("[ ", strcat(zname, " ]\n")));
		}
		pl = find(pl, classname, "player");
	}

	// 2. Activar spawners desbloqueados cercanos al jugador y pausar los lejanos
	sp = nextent(world);
	while (sp != world) {
		if (sp.classname == "spawn_zombie" || sp.classname == "spawn_zombie_in") {
			// Si una puerta acaba de activar este spawner, limpiar su flag INACTIVE
			if (sp.classname == "spawn_zombie" && (sp.spawnflags & INACTIVE)) {
				sp.spawnflags = sp.spawnflags - (sp.spawnflags & INACTIVE);
			}
			// Solo gestionar spawners ya desbloqueados
			if (!(sp.spawnflags & INACTIVE)) {
				float near = Tranzit_IsSpawnerNearAnyPlayer(sp);
				if (near) {
					if (sp.classname != "spawn_zombie") {
						sp.classname = "spawn_zombie";
						changed = true;
					}
					active_count++;
				} else {
					if (sp.classname == "spawn_zombie") {
						sp.classname = "spawn_zombie_in";
						changed = true;
					}
				}
			}
		}
		sp = nextent(sp);
	}

	// Salvaguarda: si ningun spawner quedo activo a <=1750u, activar el desbloqueado mas cercano
	if (active_count == 0) {
		pl = find(world, classname, "player");
		if (pl != world) {
			entity closest = world;
			float best_d = 999999;
			sp = find(world, classname, "spawn_zombie_in");
			while (sp != world) {
				if (!(sp.spawnflags & INACTIVE)) {
					float d = vlen(pl.origin - sp.origin);
					if (d < best_d) {
						best_d = d;
						closest = sp;
					}
				}
				sp = find(sp, classname, "spawn_zombie_in");
			}
			if (closest != world) {
				closest.classname = "spawn_zombie";
				active_count = 1;
				changed = true;
			}
		}
	}

	if (changed) {
		Zombie_ReassignSpawnIDs();
	}

	// 3. Reposicionar zombies que hayan quedado muy atras (>2300u) al viajar en Bus o Farola
	if (active_count > 0) {
		z = find(world, classname, "ai_zombie");
		while (z != world) {
			if (z.aistatus == "1" && z.health > 0) {
				float near_pl = false;
				pl = find(world, classname, "player");
				while (pl != world) {
					if (vlen(z.origin - pl.origin) <= 2300) {
						near_pl = true;
						break;
					}
					pl = find(pl, classname, "player");
				}
				if (!near_pl) {
					entity target_sp = find(world, classname, "spawn_zombie");
					if (target_sp != world) {
						setorigin(z, target_sp.origin + '0 0 2');
						z.outside = false;
						z.goalentity = world;
					}
				}
			}
			z = find(z, classname, "ai_zombie");
		}
	}

	self.nextthink = time + 1.5;
};

void() func_tranzit_director =
{
	if (mapname != "tranzit") {
		remove(self);
		return;
	}

	self.solid = SOLID_NOT;
	self.movetype = MOVETYPE_NONE;
	self.think = tranzit_director_think;
	self.nextthink = time + 2.0;
};

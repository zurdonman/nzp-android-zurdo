/*
Copyright (C) 2025-2026 NZ:P Team

This program is free software; you can redistribute it and/or
modify it under the terms of the GNU General Public License
as published by the Free Software Foundation; either version 2
of the License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

See the GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA  02111-1307, USA.
*/
// menu_coop.c -- NZ:P Android (zurdo): menu COOPERATIVE.
//
// HOST GAME abre el selector de mapas con menu_is_solo=false; Menu_LoadMap
// levanta un servidor listen con coop=1, maxplayers y sv_maxspeed acompanando
// al multiplicador de velocidad. Los clientes entran por LAN (broadcast UDP
// via net_dgrm.c + net_udp_sdl.c, protocolo CCREQ/CCREP de Quake) o por IP.
//
// ONLINE queda como PROXIMAMENTE (grisado).
#include "../nzportable_def.h"
#include "menu_defs.h"

#ifdef NZP_MENU_COOP

// net.h declara NET_Slist_f y hostcache_t/hostcache/hostCacheCount.
// slistSilent solo se exporta desde net_main.c (quieto, sin volcar a consola).
extern qboolean slistSilent;

// Estado del navegador LAN
static qboolean coop_searching = false;
static double coop_search_start = 0;
#define COOP_SEARCH_SECONDS 3.0

// Indice del servidor seleccionado en hostcache (se actualiza al navegar)
static int coop_selected_host = -1;

// Buffer para "JOIN BY IP" (se llena con el comando coopip)
static char coop_ip_buffer[64];

void Menu_Coop_Host (void)
{
	menu_is_solo = false;
	Menu_StockMaps_Set();
}

void Menu_Coop_Find (void)
{
	hostCacheCount = 0;
	coop_selected_host = -1;
	coop_searching = true;
	coop_search_start = Sys_FloatTime();
	slistSilent = true;
	NET_Slist_f();
}

void Menu_Coop_CancelSearch (void)
{
	coop_searching = false;
}

void Menu_Coop_SetCoopIP_f (void)
{
	if (Cmd_Argc() == 2) {
		Q_strncpyz(coop_ip_buffer, Cmd_Argv(1), sizeof(coop_ip_buffer));
		Con_Printf("COOP: IP lista -> %s (pulsa JOIN BY IP)\n", coop_ip_buffer);
	}
}

void Menu_Coop_JoinIP (void)
{
	if (!coop_ip_buffer[0]) {
		Con_Printf("COOP: escribe coopip <ip:puerto> en consola y vuelve a pulsar JOIN BY IP\n");
		Cbuf_AddText("echo Uso: coopip 192.168.1.50  (despues pulsa JOIN BY IP)\n");
		return;
	}
	Cbuf_AddText("disconnect\n");
	Cbuf_AddText("connect ");
	Cbuf_AddText(coop_ip_buffer);
	Cbuf_AddText("\n");
	coop_ip_buffer[0] = 0;
}

void Menu_Coop_JoinSelected (void)
{
	if (coop_selected_host < 0 || coop_selected_host >= hostCacheCount)
		return;

	Cbuf_AddText("disconnect\n");
	Cbuf_AddText("connect ");
	Cbuf_AddText(hostcache[coop_selected_host].cname);
	Cbuf_AddText("\n");
	coop_searching = false;
}

void Menu_Coop_Set (void)
{
	Menu_ResetMenuButtons();
	coop_searching = false;
	m_previous_state = m_main;
	m_state = m_coop;
}

void Menu_Coop_Back (void)
{
	coop_searching = false;
	Menu_Main_Set();
}

void Menu_Coop_Draw (void)
{
	int coop_buttons = 1;
	int coop_index = 0;
	double elapsed;

	Menu_DrawCustomBackground(true);
	Menu_DrawTitle("COOPERATIVE", MENU_COLOR_WHITE);

	// HOST GAME
	Menu_DrawButton(coop_buttons++, coop_index++, "HOST GAME", "Create a Game on this Device (LAN).", Menu_Coop_Host);

	// FIND LAN GAME: inicia busqueda y lista los resultados en vivo
	if (coop_searching) {
		elapsed = Sys_FloatTime() - coop_search_start;

		if (hostCacheCount > 0) {
			int n;
			char entry[32];
			for (n = 0; n < hostCacheCount && coop_index < (MAX_MENU_BUTTONS - 2); n++) {
				snprintf(entry, sizeof(entry), "%s %d/%d", hostcache[n].name, hostcache[n].users, hostcache[n].maxusers);
				Menu_DrawButton(coop_buttons++, coop_index++, entry, hostcache[n].cname, Menu_Coop_JoinSelected);
			}
		}

		if (elapsed < COOP_SEARCH_SECONDS) {
			Menu_DrawButton(coop_buttons++, coop_index++, "SEARCHING...", "Looking for LAN Games.", Menu_Coop_CancelSearch);
		} else {
			coop_searching = false;
			if (hostCacheCount == 0) {
				Menu_DrawButton(coop_buttons++, coop_index++, "NO GAMES FOUND", "Tap to Search Again.", Menu_Coop_Find);
			} else {
				Menu_DrawButton(coop_buttons++, coop_index++, "RE-SCAN", "Search the LAN Again.", Menu_Coop_Find);
			}
		}
	} else {
		Menu_DrawButton(coop_buttons++, coop_index++, "FIND LAN GAME", "Search for Games on your Network.", Menu_Coop_Find);
	}

	// ONLINE: proximamente (gris)
	Menu_DrawGreyButton(coop_buttons++, "ONLINE (SOON)");

	Menu_DrawDivider(coop_buttons);

	// IP directa: primero "coopip <ip>" en consola, luego pulsar aqui
	Menu_DrawButton(coop_buttons++, coop_index++, "JOIN BY IP", "Direct Connect (see Console).", Menu_Coop_JoinIP);

	// BACK
	Menu_DrawButton(-1, coop_index, "BACK", "Return to Main Menu.", Menu_Coop_Back);

	// Resolver la fila pulsada: si el cursor esta sobre una fila de la lista
	// de servidores, recuerda cual es para Menu_Coop_JoinSelected().
	if (current_menu.cursor >= 2 && current_menu.cursor < 2 + hostCacheCount) {
		coop_selected_host = current_menu.cursor - 2;
	}
}

#endif // NZP_MENU_COOP

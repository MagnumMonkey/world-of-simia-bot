export function getCardTags(card) {
  const tags = new Set();

  const text = [
    card.name,
    card.rarity,
    card.personality,
    card.status,
    Array.isArray(card.statuses) ? card.statuses.join(" ") : "",
    card.ability,
    card.skill,
    card.flavor_text,
    Array.isArray(card.tags) ? card.tags.join(" ") : ""
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();

  // Basic card info
  if (card.rarity) tags.add(card.rarity.toLowerCase());

  if (card.personality) {
    card.personality
      .toLowerCase()
      .split("/")
      .map(p => p.trim())
      .forEach(p => tags.add(p));
  }

  if (Array.isArray(card.statuses)) {
    card.statuses.forEach(status => tags.add(status.toLowerCase()));
  }

  // Factions / families
  if (text.includes("lumari")) tags.add("lumari");
  if (text.includes("inkarri")) tags.add("inkarri");

  // Phases
  if (text.includes("wake-up phase")) tags.add("wake-up phase");
  if (text.includes("ready phase")) tags.add("ready phase");
  if (text.includes("playtime phase")) tags.add("playtime phase");
  if (text.includes("snooze phase")) tags.add("snooze phase");
  if (text.includes("end phase")) tags.add("end phase");

  // Core mechanics
  if (text.includes("baseline skill")) tags.add("baseline skill");
  if (text.includes("baseline")) tags.add("baseline");
  if (text.includes("summon")) tags.add("summon");
  if (text.includes("resummon")) tags.add("resummon");
  if (text.includes("recall")) tags.add("recall");
  if (text.includes("expel")) tags.add("expel");

  if (text.includes("send") && text.includes("home")) {
    tags.add("send home");
  }

  if (text.includes("cannot be sent home")) {
    tags.add("home protection");
  }

  // Stat mechanics
  if (text.includes("banana size")) tags.add("banana size matters");
  if (text.includes("charm")) tags.add("charm matters");
  if (text.includes("mischief")) tags.add("mischief matters");
  if (text.includes("total")) tags.add("total matters");

  if (text.includes("grow") || text.includes("grows")) {
    tags.add("stat grow");
  }

  if (text.includes("shrink") || text.includes("shrinks")) {
    tags.add("stat shrink");
  }

  // Status mechanics
  if (text.includes("heat")) tags.add("heat matters");
  if (text.includes("briefs")) tags.add("briefs matters");
  if (text.includes("erect")) tags.add("erect matters");
  if (text.includes("nude")) tags.add("nude matters");
  if (text.includes("ejaculating")) tags.add("ejaculating matters");

  // Playtime mechanics
  if (text.includes("initiate")) tags.add("initiates playtime");
  if (text.includes("additional playtime")) tags.add("extra playtime");
  if (text.includes("playtime points")) tags.add("point modifier");
  if (text.includes("decided by banana size")) tags.add("banana size encounter");
  if (text.includes("decided by charm")) tags.add("charm encounter");
  if (text.includes("decided by mischief")) tags.add("mischief encounter");

  // Natural Monkey Boy
  // This handles truly blank cards.
  const hasAbility = Boolean(card.ability && card.ability.trim());
  const hasSkill = Boolean(card.skill && card.skill.trim());

  if (!hasAbility && !hasSkill) {
    tags.add("natural");
  }

  // Manual override tags from JSON
  if (Array.isArray(card.tags)) {
    card.tags.forEach(tag => tags.add(tag.toLowerCase()));
  }

  return Array.from(tags);
}
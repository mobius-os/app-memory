"""Final-graph correctness from the private writer pilot, without model calls."""
from pathlib import Path
import pytest
import memory_runner as writer


def proposal(**changes):
  return {"summary":"correction", "self_review":{k:"none" for k in (
    "hardest_decision","possibly_missed","prompt_change","next_experiment")},
    "updates":[],"deletes":[],"links":[],"read_audits":[],"followups":[],**changes}


def note(claim="Prefers morning sessions", sources="chat:old"):
  return f"---\ntype: note\ntitle: Session preference\ndescription: {claim}\nmocs: [preferences]\nsource: [{sources}]\n---\n{claim}\n"


@pytest.fixture
def graph(tmp_path,monkeypatch):
  monkeypatch.setattr(writer,"load_usage",lambda:{})
  monkeypatch.setattr(writer,"load_recall_guidance",lambda:{})
  (tmp_path/'notes').mkdir();(tmp_path/'mocs').mkdir()
  (tmp_path/'index.md').write_text('---\ntype: moc\ntitle: Memory\ndescription: Root\n---\n- [[preferences]] — Preferences\n')
  (tmp_path/'mocs/preferences.md').write_text('---\ntype: moc\ntitle: Preferences\ndescription: Preferences\n---\nUnrelated introduction.\n\n- [[time]] — Prefers morning sessions\n\n- [[privacy]] — Keep drafts private\n')
  (tmp_path/'notes/time.md').write_text(note())
  (tmp_path/'notes/privacy.md').write_text(note('Keep drafts private'))
  writer.build_graph(tmp_path,usage={})
  return tmp_path


def normalize(p):
  return writer._normalize_proposal(p,allowed_chat_ids={'old','new'},source_handles={'c01':'new'},
    existing_note_paths={'notes/time.md','notes/privacy.md'},editable_note_paths={'notes/time.md','notes/privacy.md'})


def test_correction_updates_note_and_parent_cue_preserving_unrelated_content(graph):
  before=(graph/'mocs/preferences.md').read_text()
  privacy=(graph/'notes/privacy.md').read_bytes()
  p=normalize(proposal(updates=[{'path':'notes/time.md','content':note('Prefers evening sessions; supersedes mornings','chat:old, chat:c01')}],
    links=[{'from':'mocs/preferences.md','to':'notes/time.md','cue':'Prefers evening sessions'}]))
  writer._apply_validated_proposal(graph,p,baseline=writer.build_graph(graph,usage={}))
  assert (graph/'mocs/preferences.md').read_text()==before.replace('Prefers morning sessions','Prefers evening sessions')
  assert (graph/'notes/privacy.md').read_bytes()==privacy
  assert 'chat:old, chat:new' in (graph/'notes/time.md').read_text()
  assert 'evening' in (graph/'notes/time.md').read_text()
  # Same explicit cue is idempotent; no duplicated routing entry.
  assert writer._apply_normalized_proposal(graph,normalize(proposal(links=p['links']))) == ([],[])


def test_prompt_supplies_current_routes_only_for_supplied_note_bodies(graph):
  routes=writer._note_routes(graph,{'notes/time.md'})
  assert routes==[{'from':'mocs/preferences.md','to':'notes/time.md','line':'- [[time]] — Prefers morning sessions','editable_cue':True}]
  text=writer._proposal_prompt(graph,[],[],extra_note_paths={'notes/time.md'})
  assert 'existing_note_routes' in text
  assert 'correct contradicted routing cues' in text
  assert 'never source: [chat:old, c01]' in text


@pytest.mark.parametrize('sources',['chat:old, c01','chat:old, invented','chat:old, chat:new.extra','chat:old, deleted:d99'])
def test_invalid_appended_source_cannot_hide_behind_valid_old_source(sources):
  with pytest.raises(writer.ProposalValidationError,match='every source list entry'):
    normalize(proposal(updates=[{'path':'notes/time.md','content':note(sources=sources)}]))


def test_unknown_prefixed_source_remains_rejected():
  with pytest.raises(writer.ProposalValidationError):
    normalize(proposal(updates=[{'path':'notes/time.md','content':note(sources='chat:old, chat:unknown')}]))


def test_valid_quoted_sources_and_deleted_source_expansion_preserve_provenance():
  p=writer._normalize_proposal(proposal(updates=[{'path':'notes/time.md','content':note(sources="'chat:old', \"chat:c01\", deleted:d01")}]),
    allowed_chat_ids={'old','new'},source_handles={'c01':'new'},deleted_source_handles={'d01':'a'*32},
    allowed_deleted_source_ids={'a'*32},allow_deleted_source=True)
  content=p['updates'][0]['content']
  assert 'chat:new' in content and 'deleted-chat:'+('a'*32) in content


def test_contextual_link_is_not_silently_rewritten_and_entire_batch_rolls_back(graph):
  path=graph/'mocs/preferences.md'
  path.write_text(path.read_text().replace('- [[time]] — Prefers morning sessions','Both [[time]] and [[privacy]] matter here.'))
  before=path.read_bytes();old_note=(graph/'notes/time.md').read_bytes()
  assert writer._note_routes(graph,{'notes/time.md'})[0]['editable_cue'] is False
  p=normalize(proposal(updates=[{'path':'notes/time.md','content':note('Evenings')}],links=[{'from':'mocs/preferences.md','to':'notes/time.md','cue':'Evenings'}]))
  with pytest.raises(writer.ProposalValidationError,match='contextual prose'):
    writer._apply_validated_proposal(graph,p,baseline=writer.build_graph(graph,usage={}))
  assert path.read_bytes()==before
  assert (graph/'notes/time.md').read_bytes()==old_note


def test_alias_indentation_and_newline_survive_explicit_cue_update(graph):
  path=graph/'mocs/preferences.md'
  path.write_text(path.read_text().replace('- [[time]] — Prefers morning sessions','  * [[notes/time.md|Timing]] – Prefers morning sessions'))
  before=path.read_text()
  p=normalize(proposal(links=[{'from':'mocs/preferences.md','to':'notes/time.md','cue':'Evenings'}]))
  writer._apply_normalized_proposal(graph,p)
  assert path.read_text()==before.replace('Prefers morning sessions','Evenings')


def test_protected_maps_are_never_exposed_as_editable_routes(graph):
  (graph/'mocs/maintaining-memory.md').write_text('- [[time]] — System-owned cue\n')
  assert all(r['from']!='mocs/maintaining-memory.md' for r in writer._note_routes(graph,{'notes/time.md'}))
  with pytest.raises(writer.ProposalValidationError):
    normalize(proposal(links=[{'from':'mocs/maintaining-memory.md','to':'notes/time.md','cue':'Changed'}]))


def test_missing_link_is_added_without_touching_existing_routes(graph):
  before=(graph/'index.md').read_text()
  p=normalize(proposal(links=[{'from':'index.md','to':'notes/time.md','cue':'Session timing'}]))
  writer._apply_normalized_proposal(graph,p)
  assert (graph/'index.md').read_text()==before.rstrip()+'\n\n- [[time]] — Session timing\n'


def test_later_contextual_failure_rolls_back_earlier_link_update(graph):
  root=graph/'index.md';root.write_text(root.read_text()+'Contextual [[time]] matters.\n')
  old_root=root.read_bytes();old_map=(graph/'mocs/preferences.md').read_bytes()
  p=normalize(proposal(links=[{'from':'mocs/preferences.md','to':'notes/time.md','cue':'Evenings'},
                            {'from':'index.md','to':'notes/time.md','cue':'Evenings'}]))
  with pytest.raises(writer.ProposalValidationError):
    writer._apply_validated_proposal(graph,p,baseline=writer.build_graph(graph,usage={}))
  assert root.read_bytes()==old_root
  assert (graph/'mocs/preferences.md').read_bytes()==old_map

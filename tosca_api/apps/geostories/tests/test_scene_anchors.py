"""Tests for mapScene anchors in GeoStory content."""

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client
from django.urls import reverse

from tosca_api.apps.campaigns.models import Campaign
from tosca_api.apps.geostories.models import GeoStory, GeoStoryScene

User = get_user_model()


def anchor(scene):
    return {"type": "mapScene", "data": {"scene_id": str(getattr(scene, "pk", scene))}}


def paragraph(text="Text"):
    return {"type": "paragraph", "data": {"text": text}}


@pytest.fixture
def superuser():
    return User.objects.create_user(
        username="anchor-admin", password="x", is_staff=True, is_superuser=True
    )


@pytest.fixture
def campaign(superuser):
    return Campaign.objects.create(title="Campaign", created_by=superuser)


@pytest.fixture
def story(campaign, superuser):
    return GeoStory.objects.create(
        title="Story", campaign=campaign, author=superuser, status=GeoStory.Status.PUBLISHED
    )


@pytest.fixture
def scenes(story):
    return [GeoStoryScene.objects.create(geostory=story, title=f"Scene {i}") for i in range(3)]


@pytest.fixture
def client(superuser):
    client = Client()
    client.force_login(superuser)
    return client


# --- Model validation ---------------------------------------------------------


@pytest.mark.django_db
def test_story_accepts_anchors_to_its_own_scenes(story, scenes):
    story.content = {"blocks": [paragraph(), anchor(scenes[1]), paragraph(), anchor(scenes[2])]}

    story.full_clean()
    story.save()

    story.refresh_from_db()
    assert story.anchored_scene_ids() == {str(scenes[1].pk), str(scenes[2].pk)}


@pytest.mark.django_db
def test_story_rejects_anchor_to_another_storys_scene(story, campaign, superuser):
    other = GeoStory.objects.create(title="Other", campaign=campaign, author=superuser)
    foreign = GeoStoryScene.objects.create(geostory=other, title="Foreign")
    story.content = {"blocks": [anchor(foreign)]}

    with pytest.raises(ValidationError) as exc:
        story.full_clean()

    assert "does not belong to this story" in exc.value.message_dict["content"][0]


@pytest.mark.django_db
def test_story_rejects_anchor_to_deleted_scene(story, scenes):
    story.content = {"blocks": [anchor(scenes[1])]}
    story.save()
    GeoStoryScene.objects.filter(pk=scenes[1].pk).delete()

    with pytest.raises(ValidationError) as exc:
        story.full_clean()

    assert "may have been deleted" in exc.value.message_dict["content"][0]


@pytest.mark.django_db
def test_story_rejects_duplicate_anchor(story, scenes):
    story.content = {"blocks": [anchor(scenes[1]), paragraph(), anchor(scenes[1])]}

    with pytest.raises(ValidationError) as exc:
        story.full_clean()

    assert "anchored only once" in exc.value.message_dict["content"][0]


@pytest.mark.django_db
def test_unsaved_story_cannot_carry_anchors(campaign, superuser, scenes):
    story = GeoStory(
        title="New", campaign=campaign, author=superuser, content={"blocks": [anchor(scenes[0])]}
    )

    with pytest.raises(ValidationError) as exc:
        story.full_clean()

    assert "content" in exc.value.message_dict


@pytest.mark.django_db
def test_unreachable_scenes_excludes_first_scene_and_anchored_ones(story, scenes):
    assert story.unreachable_scenes() == scenes[1:]

    story.content = {"blocks": [anchor(scenes[2])]}
    story.save()

    assert story.unreachable_scenes() == [scenes[1]]


@pytest.mark.django_db
def test_other_features_still_reject_map_scene_blocks(campaign, superuser):
    from tosca_api.apps.feedback.models import GeoFeedback

    feedback = GeoFeedback(
        campaign=campaign,
        title="Feedback",
        rating_enabled=True,
        created_by=superuser,
        content={"blocks": [anchor("0192f0c8-1b2a-7c3d-8e4f-5a6b7c8d9e0f")]},
    )

    with pytest.raises(ValidationError):
        feedback.full_clean()


@pytest.mark.django_db
def test_detail_api_returns_anchor_blocks(story, scenes):
    from rest_framework.test import APIClient

    story.content = {"blocks": [paragraph(), anchor(scenes[1])]}
    story.save()

    response = APIClient().get(f"/api/v1/stories/{story.id}/")

    assert response.data["content"]["blocks"][1] == anchor(scenes[1])


@pytest.mark.django_db
def test_preflight_accepts_anchors_in_story_content(story, scenes, capsys):
    story.content = {"blocks": [anchor(scenes[1])]}
    story.save()

    call_command("content_preflight")

    assert "No failures detected" in capsys.readouterr().out


# --- Admin --------------------------------------------------------------------


@pytest.mark.django_db
def test_story_editor_receives_scene_list(client, story, scenes):
    import json

    response = client.get(reverse("admin:geostories_geostory_change", args=[story.pk]))

    widget_attrs = response.context["adminform"].form.fields["content"].widget.attrs
    offered = json.loads(widget_attrs["data-editorjs-map-scenes"])
    assert [scene["id"] for scene in offered] == [str(scene.pk) for scene in scenes]
    assert offered[0]["url"] == reverse(
        "admin:geostories_geostoryscene_change", args=[scenes[0].pk]
    )
    assert "geostories/js/editorjs_map_scene.js" in response.content.decode()


@pytest.mark.django_db
def test_story_page_flags_unanchored_scenes(client, story, scenes):
    story.content = {"blocks": [anchor(scenes[2])]}
    story.save()

    content = client.get(
        reverse("admin:geostories_geostory_change", args=[story.pk])
    ).content.decode()

    assert "Readers will never see “Scene 1”" in content
    assert content.count("Not anchored — never shown") == 1
    assert "Shown before the first anchor" in content
    assert "Anchored" in content


@pytest.mark.django_db
def test_story_admin_save_reports_anchor_errors(client, story, scenes, campaign, superuser):
    import json

    other = GeoStory.objects.create(title="Other", campaign=campaign, author=superuser)
    foreign = GeoStoryScene.objects.create(geostory=other, title="Foreign")
    data = {
        "title": story.title,
        "summary": "",
        "status": story.status,
        "campaign": str(campaign.pk),
        "author": str(superuser.pk),
        "about_author": "",
        "content": json.dumps({"blocks": [anchor(foreign)]}),
        "hero_image_alt": "",
        "scenes-TOTAL_FORMS": "3",
        "scenes-INITIAL_FORMS": "3",
        "scenes-MIN_NUM_FORMS": "0",
        "scenes-MAX_NUM_FORMS": "1000",
    }
    for index, scene in enumerate(scenes):
        data[f"scenes-{index}-id"] = str(scene.pk)
        data[f"scenes-{index}-geostory"] = str(story.pk)

    response = client.post(reverse("admin:geostories_geostory_change", args=[story.pk]), data)

    assert response.status_code == 200
    assert "does not belong to this story" in response.content.decode()


@pytest.mark.django_db
def test_anchored_scene_delete_is_blocked(client, story, scenes):
    story.content = {"blocks": [anchor(scenes[1])]}
    story.save()
    url = reverse("admin:geostories_geostoryscene_delete", args=[scenes[1].pk])

    page = client.get(url).content.decode()
    response = client.post(url, {"post": "yes"})

    assert "remove the anchor first" in page
    # Django re-renders the confirmation page instead of deleting protected objects.
    assert response.status_code == 200
    assert "remove the anchor first" in response.content.decode()
    assert GeoStoryScene.objects.filter(pk=scenes[1].pk).exists()


@pytest.mark.django_db
def test_unanchored_scene_can_be_deleted(client, scenes):
    url = reverse("admin:geostories_geostoryscene_delete", args=[scenes[2].pk])

    response = client.post(url, {"post": "yes"})

    assert response.status_code == 302
    assert not GeoStoryScene.objects.filter(pk=scenes[2].pk).exists()


@pytest.mark.django_db
def test_deleting_story_with_anchors_still_cascades(story, scenes):
    story.content = {"blocks": [anchor(scenes[1])]}
    story.save()

    story.delete()

    assert not GeoStoryScene.objects.exists()

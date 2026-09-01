"""Input validation for catalogue interactions."""

from django import forms

from .models import RATING_MAX, RATING_MIN, Comment


class RatingForm(forms.Form):
    score = forms.IntegerField(min_value=RATING_MIN, max_value=RATING_MAX)


class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 3, "placeholder": "Share your thoughts on this episode", "maxlength": 2000}
            )
        }
        labels = {"body": "Your comment"}

    def clean_body(self) -> str:
        body = (self.cleaned_data.get("body") or "").strip()
        if not body:
            raise forms.ValidationError("Write something before posting.")
        return body

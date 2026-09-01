from django.urls import path

from . import views

app_name = "shop"

urlpatterns = [
    path("", views.ProductListView.as_view(), name="product-list"),
    path("cart/", views.CartView.as_view(), name="cart"),
    path("cart/update/<slug:slug>/", views.update_cart_view, name="cart-update"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("orders/", views.OrderListView.as_view(), name="order-list"),
    path("orders/<str:reference>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:reference>/cancel/", views.cancel_order_view, name="order-cancel"),
    path("pay/<str:reference>/", views.start_payment_view, name="payment-start"),
    path("pay/<str:reference>/return/", views.payment_return_view, name="payment-return"),
    path("pay/<str:reference>/ipn/", views.payment_ipn_view, name="payment-ipn"),
    path("product/<slug:slug>/", views.ProductDetailView.as_view(), name="product-detail"),
]
